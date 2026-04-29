#!/usr/bin/env python3
"""
Clasificador binario menor/adulto — MobileNetV2 + transfer learning.
Dataset: carpetas nombradas con la edad (p.ej. "017"), imágenes PNG dentro.
Salida:  age-detection/model/age_classifier.keras
         reports/training/training_report.json

Velocidad orientativa:
  CPU  : ~25-35 min
  GPU  : ~8-12 min
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
from sklearn.metrics import classification_report, confusion_matrix, roc_auc_score
from sklearn.model_selection import train_test_split

os.environ["TF_CPP_MIN_LOG_LEVEL"] = "2"
import tensorflow as tf
from tensorflow.keras import callbacks, layers, models
from tensorflow.keras.applications import MobileNetV2

# ── Rutas ─────────────────────────────────────────────────────────────────────
_root       = Path(__file__).parent.parent
DATASET_DIR = Path(os.environ.get("DATASET_DIR", str(_root / "dataset")))
OUTPUT_DIR  = Path(os.environ.get("OUTPUT_DIR",  str(_root / "age-detection" / "model")))
REPORTS_DIR = Path(os.environ.get("REPORTS_DIR", str(_root / "reports" / "training")))
MODEL_PATH  = OUTPUT_DIR / "age_classifier.keras"

# ── Hiperparámetros ───────────────────────────────────────────────────────────
IMG_SIZE          = (200, 200)   # debe coincidir con age-detection/main.py IMG_SIZE
BATCH_SIZE        = 32
EPOCHS_HEAD       = 20
EPOCHS_FINETUNE   = 15
LR_HEAD           = 1e-3
LR_FINETUNE       = 5e-6
MINOR_THRESHOLD   = 18
VAL_SPLIT         = 0.2
SEED              = 42
MAX_IMGS_PER_AGE  = 120          # cap agresivo: reduce dominio de bebés
AGE_1_CAP         = 40           # edad 1 = 28% de los menores → cap muy agresivo
BORDERLINE_AGES   = set(range(13, 18))   # edades críticas: la frontera real
BORDERLINE_REPEAT = 5            # oversample 5× las edades 13-17
CLASS_WEIGHT_MINOR = 4.0         # penaliza 4× no detectar un menor

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger("train")

# ── Dataset ───────────────────────────────────────────────────────────────────

def load_raw_dataset(dataset_dir: Path):
    paths, labels, ages = [], [], []
    counts = {0: 0, 1: 0}
    rng = np.random.default_rng(SEED)
    for age_dir in sorted(dataset_dir.iterdir()):
        if not age_dir.is_dir():
            continue
        name = age_dir.name.lstrip("0") or "0"
        if not name.isdigit():
            continue
        age   = int(name)
        label = 1 if age < MINOR_THRESHOLD else 0
        imgs  = list(age_dir.glob("*.png"))
        cap   = AGE_1_CAP if age == 1 else MAX_IMGS_PER_AGE
        if len(imgs) > cap:
            imgs = [imgs[i] for i in rng.choice(len(imgs), cap, replace=False)]
        for p in imgs:
            paths.append(str(p))
            labels.append(label)
            ages.append(age)
            counts[label] += 1
    log.info("Dataset: %d imgs | menores: %d | adultos: %d", len(paths), counts[1], counts[0])
    return np.array(paths), np.array(labels, dtype=np.int32), np.array(ages, dtype=np.int32)


def oversample_borderline(paths, labels, ages):
    extra_p, extra_l = [], []
    for p, l, a in zip(paths, labels, ages):
        if a in BORDERLINE_AGES:
            for _ in range(BORDERLINE_REPEAT - 1):
                extra_p.append(p)
                extra_l.append(l)
    if extra_p:
        paths  = np.concatenate([paths,  extra_p])
        labels = np.concatenate([labels, extra_l])
        log.info("Oversampling borderline %s: +%d imgs → %d total", sorted(BORDERLINE_AGES), len(extra_p), len(paths))
    return paths, labels


def preprocess(path):
    img = tf.io.read_file(path)
    img = tf.image.decode_png(img, channels=3)
    img = tf.image.resize(img, IMG_SIZE)
    return tf.keras.applications.mobilenet_v2.preprocess_input(img)


def augment(img):
    img = tf.image.random_flip_left_right(img)
    img = tf.image.random_brightness(img, max_delta=0.25)
    img = tf.image.random_contrast(img, lower=0.75, upper=1.25)
    img = tf.image.random_saturation(img, lower=0.75, upper=1.25)
    img = tf.image.random_hue(img, max_delta=0.08)
    return tf.clip_by_value(img, -1.0, 1.0)


def make_dataset(paths, labels, augment_data=False, shuffle=False):
    ds = tf.data.Dataset.from_tensor_slices((paths, labels))
    if shuffle:
        ds = ds.shuffle(buffer_size=len(paths), seed=SEED)
    ds = ds.map(lambda p, l: (preprocess(p), l), num_parallel_calls=tf.data.AUTOTUNE)
    if augment_data:
        ds = ds.map(lambda img, l: (augment(img), l), num_parallel_calls=tf.data.AUTOTUNE)
    return ds.batch(BATCH_SIZE).prefetch(tf.data.AUTOTUNE)

# ── Modelo ────────────────────────────────────────────────────────────────────

def build_model(trainable_base=False):
    base = MobileNetV2(input_shape=(*IMG_SIZE, 3), include_top=False, weights="imagenet")
    base.trainable = trainable_base

    inp = tf.keras.Input(shape=(*IMG_SIZE, 3))
    x   = base(inp, training=False)
    x   = layers.GlobalAveragePooling2D()(x)
    x   = layers.Dense(256, activation="relu")(x)
    x   = layers.BatchNormalization()(x)
    x   = layers.Dropout(0.5)(x)
    x   = layers.Dense(128, activation="relu")(x)
    x   = layers.BatchNormalization()(x)
    x   = layers.Dropout(0.3)(x)
    out = layers.Dense(1, activation="sigmoid", name="minor_prob")(x)
    return models.Model(inp, out, name="age_classifier")

# ── Evaluación ────────────────────────────────────────────────────────────────

def evaluate(model, val_ds, val_labels):
    probs = model.predict(val_ds, verbose=0).ravel()
    auc   = roc_auc_score(val_labels, probs)
    log.info("AUC-ROC: %.4f", auc)

    threshold_results = {}
    best_thr, best_recall = 0.5, 0.0

    for thr in [0.3, 0.4, 0.5, 0.6]:
        preds = (probs >= thr).astype(int)
        r     = classification_report(val_labels, preds,
                                      target_names=["adulto(0)", "menor(1)"],
                                      output_dict=True)
        cm    = confusion_matrix(val_labels, preds)
        log.info("\n── Threshold %.1f ──\n%s\n%s", thr,
                 classification_report(val_labels, preds, target_names=["adulto(0)", "menor(1)"]), cm)
        threshold_results[str(thr)] = {"classification": r, "confusion_matrix": cm.tolist()}
        if r["menor(1)"]["precision"] >= 0.60 and r["menor(1)"]["recall"] > best_recall:
            best_recall = r["menor(1)"]["recall"]
            best_thr    = thr

    log.info("Threshold recomendado: %.1f  (recall menores: %.4f)", best_thr, best_recall)
    return {
        "auc_roc":                            round(float(auc), 4),
        "thresholds":                         threshold_results,
        "recommended_threshold":              best_thr,
        "recommended_threshold_recall_minor": round(best_recall, 4),
    }


def plot_history(h1, h2, out_dir: Path):
    fig, axes = plt.subplots(1, 2, figsize=(14, 5))
    boundary  = len(h1.history["accuracy"])
    for ax, (train_m, val_m), title in zip(
        axes,
        [(h1.history["accuracy"]  + h2.history["accuracy"],
          h1.history["val_accuracy"] + h2.history["val_accuracy"]),
         (h1.history["loss"]      + h2.history["loss"],
          h1.history["val_loss"]  + h2.history["val_loss"])],
        ["Accuracy", "Loss"],
    ):
        ax.plot(train_m, label="train")
        ax.plot(val_m,   label="val")
        ax.axvline(boundary - 1, color="gray", linestyle="--", label="fine-tune start")
        ax.set_title(title); ax.set_xlabel("Epoch"); ax.legend()
    fig.tight_layout()
    fig.savefig(out_dir / "training_curves.png", dpi=120)
    plt.close(fig)

# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    gpu = tf.config.list_physical_devices("GPU")
    log.info("TensorFlow %s | GPU: %s", tf.__version__, gpu or "NINGUNA — entrenando en CPU")

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    all_paths, all_labels, all_ages = load_raw_dataset(DATASET_DIR)

    tr_paths, val_paths, tr_labels, val_labels, tr_ages, _ = train_test_split(
        all_paths, all_labels, all_ages,
        test_size=VAL_SPLIT, stratify=all_labels, random_state=SEED,
    )
    log.info("Train base: %d | Val: %d", len(tr_paths), len(val_paths))

    tr_paths, tr_labels = oversample_borderline(tr_paths, tr_labels, tr_ages)

    class_weights = {0: 1.0, 1: CLASS_WEIGHT_MINOR}
    train_ds = make_dataset(tr_paths,  tr_labels,  augment_data=True,  shuffle=True)
    val_ds   = make_dataset(val_paths, val_labels, augment_data=False, shuffle=False)

    # Focal Loss: down-pondera los ejemplos fáciles (bebés obvios) y se centra
    # en los difíciles (teens 15-17). gamma=2 es el valor estándar.
    loss_fn = tf.keras.losses.BinaryFocalCrossentropy(gamma=2.0, from_logits=False)
    metrics = [
        "accuracy",
        tf.keras.metrics.AUC(name="auc"),
        tf.keras.metrics.Recall(name="recall"),
        tf.keras.metrics.Precision(name="precision"),
    ]

    # ── Fase 1: cabeza ────────────────────────────────────────────────────────
    log.info("=" * 55)
    log.info("FASE 1 — cabeza (%d épocas máx.)", EPOCHS_HEAD)
    log.info("=" * 55)

    model = build_model(trainable_base=False)
    model.compile(optimizer=tf.keras.optimizers.Adam(LR_HEAD), loss=loss_fn, metrics=metrics)

    h1 = model.fit(
        train_ds, validation_data=val_ds,
        epochs=EPOCHS_HEAD, class_weight=class_weights,
        callbacks=[
            callbacks.EarlyStopping(monitor="val_loss", patience=5,
                                    restore_best_weights=True, verbose=1),
            callbacks.ReduceLROnPlateau(monitor="val_loss", factor=0.5, patience=2, verbose=1),
        ],
        verbose=1,
    )

    # ── Fase 2: fine-tuning ───────────────────────────────────────────────────
    log.info("=" * 55)
    log.info("FASE 2 — fine-tuning últimas 30 capas (%d épocas máx.)", EPOCHS_FINETUNE)
    log.info("=" * 55)

    base = model.layers[1]
    base.trainable = True
    for layer in base.layers[:-30]:
        layer.trainable = False

    model.compile(optimizer=tf.keras.optimizers.Adam(LR_FINETUNE), loss=loss_fn, metrics=metrics)

    h2 = model.fit(
        train_ds, validation_data=val_ds,
        epochs=EPOCHS_FINETUNE, class_weight=class_weights,
        callbacks=[
            callbacks.EarlyStopping(monitor="val_loss", patience=5,
                                    restore_best_weights=True, verbose=1),
            callbacks.ModelCheckpoint(str(MODEL_PATH), monitor="val_recall", mode="max",
                                      save_best_only=True, verbose=1),
            callbacks.ReduceLROnPlateau(monitor="val_loss", factor=0.3, patience=2, verbose=1),
        ],
        verbose=1,
    )

    if not MODEL_PATH.exists():
        model.save(MODEL_PATH)

    # ── Evaluación ────────────────────────────────────────────────────────────
    log.info("=" * 55)
    log.info("EVALUACIÓN FINAL")
    log.info("=" * 55)

    best_model = tf.keras.models.load_model(MODEL_PATH)
    result = evaluate(best_model, val_ds, val_labels)

    from datetime import datetime
    report = {
        "generated_at":             datetime.now().isoformat(),
        "img_size":                 list(IMG_SIZE),
        "batch_size":               BATCH_SIZE,
        "max_imgs_per_age":         MAX_IMGS_PER_AGE,
        "age_1_cap":                AGE_1_CAP,
        "borderline_ages":          sorted(BORDERLINE_AGES),
        "borderline_repeat":        BORDERLINE_REPEAT,
        "class_weight_minor":       CLASS_WEIGHT_MINOR,
        "train_samples":            len(tr_paths),
        "val_samples":              len(val_paths),
        **result,
    }
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    json_str = json.dumps(report, indent=2)
    (OUTPUT_DIR  / "training_report.json").write_text(json_str)
    (REPORTS_DIR / "training_report.json").write_text(json_str)

    plot_history(h1, h2, REPORTS_DIR)

    log.info("Modelo: %s", MODEL_PATH)
    log.info("→ Ajusta MINOR_PROB_THRESHOLD=%.1f en docker-compose.yml", result["recommended_threshold"])


if __name__ == "__main__":
    main()
