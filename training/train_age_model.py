#!/usr/bin/env python3
"""
Entrena un clasificador binario menor/adulto usando MobileNetV2 con transfer learning.
Dataset: carpetas nombradas con la edad (p.ej. "017"), imágenes PNG dentro.
Salida:  age-detection/model/age_classifier.keras  (modelo completo)
         age-detection/model/training_report.json  (métricas finales)
"""

import json
import logging
import os
import sys
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from sklearn.metrics import (
    classification_report,
    confusion_matrix,
    roc_auc_score,
)
from sklearn.model_selection import train_test_split
from sklearn.utils.class_weight import compute_class_weight

os.environ["TF_CPP_MIN_LOG_LEVEL"] = "2"
import tensorflow as tf
from tensorflow.keras import callbacks, layers, models
from tensorflow.keras.applications import MobileNetV2

# ── Configuración ─────────────────────────────────────────────────────────────

_default_dataset = Path(__file__).parent.parent / "dataset"
_default_output  = Path(__file__).parent.parent / "age-detection" / "model"
_default_reports = Path(__file__).parent.parent / "reports" / "training"

DATASET_DIR = Path(os.environ.get("DATASET_DIR", str(_default_dataset)))
OUTPUT_DIR  = Path(os.environ.get("OUTPUT_DIR",  str(_default_output)))
REPORTS_DIR = Path(os.environ.get("REPORTS_DIR", str(_default_reports)))
MODEL_PATH  = OUTPUT_DIR / "age_classifier.keras"

# Imágenes reales son 200×200 — usamos la resolución nativa para no perder información.
# MobileNetV2 acepta cualquier tamaño >= 32.
IMG_SIZE         = (200, 200)
BATCH_SIZE       = 32
EPOCHS_HEAD      = 20       # fase 1: solo la cabeza (base congelada)
EPOCHS_FINETUNE  = 15       # fase 2: descongelar últimas capas
LR_HEAD          = 1e-3
LR_FINETUNE      = 1e-5
MINOR_THRESHOLD  = 18       # edad < 18 → menor (clase 1)
VAL_SPLIT        = 0.2
SEED             = 42
# Edad 1 tiene 1112 imgs (28% de todos los menores). Se limita para evitar sesgo.
MAX_IMGS_PER_AGE = 250

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger("train")

# ── Carga del dataset ─────────────────────────────────────────────────────────

def load_paths_and_labels(dataset_dir: Path):
    filepaths, labels = [], []
    class_counts = {0: 0, 1: 0}
    capped_ages = []

    rng = np.random.default_rng(SEED)

    for age_dir in sorted(dataset_dir.iterdir()):
        if not age_dir.is_dir():
            continue
        folder_name = age_dir.name.lstrip("0")
        if not folder_name:
            folder_name = "0"
        age = int(folder_name)
        label = 1 if age < MINOR_THRESHOLD else 0  # 1=menor, 0=adulto

        age_imgs = list(age_dir.glob("*.png"))

        # Limitar edades con demasiadas imágenes para evitar sesgo
        if len(age_imgs) > MAX_IMGS_PER_AGE:
            indices  = rng.choice(len(age_imgs), MAX_IMGS_PER_AGE, replace=False)
            age_imgs = [age_imgs[i] for i in indices]
            capped_ages.append((age, len(list(age_dir.glob("*.png"))), MAX_IMGS_PER_AGE))

        for img_path in age_imgs:
            filepaths.append(str(img_path))
            labels.append(label)
            class_counts[label] += 1

    if capped_ages:
        for age, original, capped in capped_ages:
            log.info("  Edad %d: limitada de %d a %d imgs (MAX_IMGS_PER_AGE)", age, original, capped)

    log.info(
        "Dataset cargado: %d imágenes | menores (1): %d | adultos (0): %d",
        len(filepaths), class_counts[1], class_counts[0],
    )
    return np.array(filepaths), np.array(labels, dtype=np.int32)


def load_and_preprocess(path: str):
    """Lee una imagen PNG, la redimensiona y normaliza a [0, 1]."""
    img = tf.io.read_file(path)
    img = tf.image.decode_png(img, channels=3)
    img = tf.image.resize(img, IMG_SIZE)
    img = tf.cast(img, tf.float32) / 255.0
    return img


def augment(img):
    img = tf.image.random_flip_left_right(img)
    img = tf.image.random_brightness(img, max_delta=0.15)
    img = tf.image.random_contrast(img, lower=0.85, upper=1.15)
    img = tf.image.random_saturation(img, lower=0.85, upper=1.15)
    img = tf.clip_by_value(img, 0.0, 1.0)
    return img


def make_dataset(paths, labels, augment_data=False, shuffle=False):
    ds = tf.data.Dataset.from_tensor_slices((paths, labels))
    if shuffle:
        ds = ds.shuffle(buffer_size=len(paths), seed=SEED)
    ds = ds.map(
        lambda p, l: (load_and_preprocess(p), l),
        num_parallel_calls=tf.data.AUTOTUNE,
    )
    if augment_data:
        ds = ds.map(
            lambda img, l: (augment(img), l),
            num_parallel_calls=tf.data.AUTOTUNE,
        )
    ds = ds.batch(BATCH_SIZE).prefetch(tf.data.AUTOTUNE)
    return ds

# ── Arquitectura ──────────────────────────────────────────────────────────────

def build_model(trainable_base=False):
    base = MobileNetV2(
        input_shape=(*IMG_SIZE, 3),
        include_top=False,
        weights="imagenet",
    )
    base.trainable = trainable_base

    inputs = tf.keras.Input(shape=(*IMG_SIZE, 3))
    x = base(inputs, training=False)
    x = layers.GlobalAveragePooling2D()(x)
    x = layers.Dense(256, activation="relu")(x)
    x = layers.Dropout(0.4)(x)
    x = layers.Dense(64, activation="relu")(x)
    x = layers.Dropout(0.2)(x)
    outputs = layers.Dense(1, activation="sigmoid", name="minor_prob")(x)

    return models.Model(inputs, outputs, name="age_classifier")

# ── Métricas y gráficos ───────────────────────────────────────────────────────

def evaluate_model(model, val_ds, val_paths, val_labels):
    preds_prob = model.predict(val_ds, verbose=0).ravel()
    preds_bin  = (preds_prob >= 0.5).astype(int)

    auc  = roc_auc_score(val_labels, preds_prob)
    report_dict = classification_report(
        val_labels, preds_bin,
        target_names=["adulto (0)", "menor (1)"],
        output_dict=True,
    )
    cm = confusion_matrix(val_labels, preds_bin)

    log.info("AUC-ROC: %.4f", auc)
    log.info("\n%s", classification_report(
        val_labels, preds_bin,
        target_names=["adulto (0)", "menor (1)"],
    ))
    log.info("Matriz de confusión:\n%s", cm)

    return {
        "auc_roc":           round(float(auc), 4),
        "classification":    report_dict,
        "confusion_matrix":  cm.tolist(),
    }


def plot_history(h1, h2, output_dir: Path):
    fig, axes = plt.subplots(1, 2, figsize=(14, 5))

    acc  = h1.history["accuracy"]     + h2.history["accuracy"]
    vacc = h1.history["val_accuracy"] + h2.history["val_accuracy"]
    loss = h1.history["loss"]         + h2.history["loss"]
    vloss= h1.history["val_loss"]     + h2.history["val_loss"]

    boundary = len(h1.history["accuracy"])

    for ax, metric, title in zip(
        axes,
        [(acc, vacc), (loss, vloss)],
        ["Accuracy", "Loss"],
    ):
        train_m, val_m = metric
        ax.plot(train_m, label="train")
        ax.plot(val_m, label="val")
        ax.axvline(boundary - 1, color="gray", linestyle="--", label="fine-tune start")
        ax.set_title(title)
        ax.set_xlabel("Epoch")
        ax.legend()

    fig.tight_layout()
    out = output_dir / "training_curves.png"
    fig.savefig(out, dpi=120)
    log.info("Curvas guardadas en %s", out)
    plt.close(fig)

# ── Entrenamiento ─────────────────────────────────────────────────────────────

def main():
    log.info("TensorFlow %s | GPU disponible: %s", tf.__version__, bool(tf.config.list_physical_devices("GPU")))

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    # 1. Cargar rutas y etiquetas
    all_paths, all_labels = load_paths_and_labels(DATASET_DIR)

    # 2. Split estratificado
    train_paths, val_paths, train_labels, val_labels = train_test_split(
        all_paths, all_labels,
        test_size=VAL_SPLIT,
        stratify=all_labels,
        random_state=SEED,
    )
    log.info("Train: %d | Val: %d", len(train_paths), len(val_paths))

    # 3. Class weights (corrige el desbalance menores/adultos)
    class_weights_arr = compute_class_weight(
        class_weight="balanced",
        classes=np.unique(train_labels),
        y=train_labels,
    )
    class_weights = dict(enumerate(class_weights_arr))
    log.info("Class weights: %s", class_weights)

    # 4. Datasets tf.data
    train_ds = make_dataset(train_paths, train_labels, augment_data=True, shuffle=True)
    val_ds   = make_dataset(val_paths,   val_labels,   augment_data=False, shuffle=False)

    # 5. ── FASE 1: entrenar solo la cabeza (base congelada) ──────────────────
    log.info("=" * 60)
    log.info("FASE 1 — Entrenamiento de la cabeza (%d epochs)", EPOCHS_HEAD)
    log.info("=" * 60)

    model = build_model(trainable_base=False)
    model.compile(
        optimizer=tf.keras.optimizers.Adam(LR_HEAD),
        loss="binary_crossentropy",
        metrics=[
            "accuracy",
            tf.keras.metrics.AUC(name="auc"),
            tf.keras.metrics.Recall(name="recall"),
            tf.keras.metrics.Precision(name="precision"),
        ],
    )
    model.summary(print_fn=log.info)

    cb_head = [
        callbacks.EarlyStopping(
            monitor="val_recall", mode="max",
            patience=5, restore_best_weights=True, verbose=1,
        ),
        callbacks.ReduceLROnPlateau(
            monitor="val_loss", factor=0.5, patience=3, verbose=1,
        ),
    ]

    history1 = model.fit(
        train_ds,
        validation_data=val_ds,
        epochs=EPOCHS_HEAD,
        class_weight=class_weights,
        callbacks=cb_head,
        verbose=1,
    )

    # 6. ── FASE 2: fine-tuning (descongelar últimas 40 capas de MobileNetV2) ─
    log.info("=" * 60)
    log.info("FASE 2 — Fine-tuning (%d epochs)", EPOCHS_FINETUNE)
    log.info("=" * 60)

    base_model = model.layers[1]  # MobileNetV2
    base_model.trainable = True
    for layer in base_model.layers[:-40]:
        layer.trainable = False

    model.compile(
        optimizer=tf.keras.optimizers.Adam(LR_FINETUNE),
        loss="binary_crossentropy",
        metrics=[
            "accuracy",
            tf.keras.metrics.AUC(name="auc"),
            tf.keras.metrics.Recall(name="recall"),
            tf.keras.metrics.Precision(name="precision"),
        ],
    )

    cb_finetune = [
        callbacks.EarlyStopping(
            monitor="val_auc", mode="max",
            patience=6, restore_best_weights=True, verbose=1,
        ),
        callbacks.ModelCheckpoint(
            filepath=str(MODEL_PATH),
            monitor="val_auc", mode="max",
            save_best_only=True, verbose=1,
        ),
        callbacks.ReduceLROnPlateau(
            monitor="val_loss", factor=0.3, patience=3, verbose=1,
        ),
    ]

    history2 = model.fit(
        train_ds,
        validation_data=val_ds,
        epochs=EPOCHS_FINETUNE,
        class_weight=class_weights,
        callbacks=cb_finetune,
        verbose=1,
    )

    # 7. Guardar modelo final (si ModelCheckpoint no lo guardó en early stop)
    if not MODEL_PATH.exists():
        model.save(MODEL_PATH)
        log.info("Modelo guardado en %s", MODEL_PATH)
    else:
        log.info("Mejor modelo ya guardado por ModelCheckpoint en %s", MODEL_PATH)

    # 8. Evaluación final
    log.info("=" * 60)
    log.info("EVALUACIÓN FINAL")
    log.info("=" * 60)

    best_model = tf.keras.models.load_model(MODEL_PATH)
    metrics = evaluate_model(best_model, val_ds, val_paths, val_labels)

    # 9. Guardar reporte y curvas
    from datetime import datetime
    report = {
        "generated_at":      datetime.now().isoformat(),
        "img_size":          list(IMG_SIZE),
        "batch_size":        BATCH_SIZE,
        "max_imgs_per_age":  MAX_IMGS_PER_AGE,
        "epochs_head":       EPOCHS_HEAD,
        "epochs_finetune":   EPOCHS_FINETUNE,
        "minor_threshold":   MINOR_THRESHOLD,
        "train_samples":     len(train_paths),
        "val_samples":       len(val_paths),
        "class_weights":     {str(k): round(v, 4) for k, v in class_weights.items()},
        **metrics,
    }

    # Guardar en ambas rutas: junto al modelo Y en reports/training/
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    report_json = json.dumps(report, indent=2)
    (OUTPUT_DIR / "training_report.json").write_text(report_json)
    (REPORTS_DIR / "training_report.json").write_text(report_json)
    log.info("Reporte guardado en %s", REPORTS_DIR / "training_report.json")

    plot_history(history1, history2, REPORTS_DIR)

    log.info("Entrenamiento completado.")
    log.info("Modelo listo en:    %s", MODEL_PATH)
    log.info("Reportes en:        %s", REPORTS_DIR)


if __name__ == "__main__":
    main()
