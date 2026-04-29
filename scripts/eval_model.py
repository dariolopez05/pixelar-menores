#!/usr/bin/env python3
"""
Evalúa age_classifier.keras contra el dataset local.
Uso:
    python scripts/eval_model.py
    python scripts/eval_model.py --model age-detection/model/age_classifier.keras
    python scripts/eval_model.py --samples 500   # eval rápida con muestra
"""
import argparse
import os
import sys
from pathlib import Path

import numpy as np
from sklearn.metrics import (
    classification_report, confusion_matrix, roc_auc_score
)
from sklearn.model_selection import train_test_split

os.environ["TF_CPP_MIN_LOG_LEVEL"] = "2"
import tensorflow as tf

# ── Rutas por defecto ─────────────────────────────────────────────────────────
ROOT        = Path(__file__).parent.parent
MODEL_PATH  = ROOT / "age-detection" / "model" / "age_classifier.keras"
DATASET_DIR = ROOT / "dataset"
IMG_SIZE    = (200, 200)
BATCH_SIZE  = 64
MINOR_THR   = 18
SEED        = 42


def load_dataset(dataset_dir: Path, max_per_age: int | None = None, seed: int = SEED):
    paths, labels = [], []
    rng = np.random.default_rng(seed)
    for age_dir in sorted(dataset_dir.iterdir()):
        if not age_dir.is_dir():
            continue
        name = age_dir.name.lstrip("0") or "0"
        if not name.isdigit():
            continue
        age   = int(name)
        label = 1 if age < MINOR_THR else 0
        imgs  = list(age_dir.glob("*.png"))
        if max_per_age and len(imgs) > max_per_age:
            imgs = [imgs[i] for i in rng.choice(len(imgs), max_per_age, replace=False)]
        for p in imgs:
            paths.append(str(p))
            labels.append(label)
    return np.array(paths), np.array(labels, dtype=np.int32)


def make_ds(paths, labels):
    ds = tf.data.Dataset.from_tensor_slices((paths, labels))
    ds = ds.map(
        lambda p, l: (
            tf.keras.applications.mobilenet_v2.preprocess_input(
                tf.image.resize(tf.image.decode_png(tf.io.read_file(p), channels=3), IMG_SIZE)
            ),
            l
        ),
        num_parallel_calls=tf.data.AUTOTUNE,
    )
    return ds.batch(BATCH_SIZE).prefetch(tf.data.AUTOTUNE)


def evaluate(model_path: Path, dataset_dir: Path, n_samples: int | None):
    print(f"\nModelo  : {model_path}")
    print(f"Dataset : {dataset_dir}")
    print(f"TF      : {tf.__version__}\n")

    if not model_path.exists():
        sys.exit(f"ERROR: no se encuentra el modelo en {model_path}")
    if not dataset_dir.exists():
        sys.exit(f"ERROR: no se encuentra el dataset en {dataset_dir}")

    # ── Carga de datos ────────────────────────────────────────────────────────
    cap = None
    if n_samples:
        # Estimación: si queremos n_samples en total, cap ≈ n_samples / 99 edades
        cap = max(10, n_samples // 99)

    all_paths, all_labels = load_dataset(dataset_dir, max_per_age=cap)
    print(f"Imágenes cargadas : {len(all_paths)}")
    print(f"  Menores (1)     : {all_labels.sum()}")
    print(f"  Adultos (0)     : {(all_labels == 0).sum()}")

    # Mismo split 80/20 que en training para evaluar en datos "no vistos"
    _, val_paths, _, val_labels = train_test_split(
        all_paths, all_labels, test_size=0.2, stratify=all_labels, random_state=SEED
    )
    print(f"\nValidación        : {len(val_paths)} imgs")

    val_ds = make_ds(val_paths, val_labels)

    # ── Inferencia ────────────────────────────────────────────────────────────
    print("\nCargando modelo...")
    model  = tf.keras.models.load_model(model_path)
    print("Ejecutando predicciones...")
    probs  = model.predict(val_ds, verbose=1).ravel()

    # ── Resultados ────────────────────────────────────────────────────────────
    auc = roc_auc_score(val_labels, probs)
    print(f"\n{'='*55}")
    print(f"  AUC-ROC : {auc:.4f}")
    print(f"{'='*55}")

    best_thr, best_recall = 0.5, 0.0

    for thr in [0.3, 0.4, 0.5, 0.6]:
        preds  = (probs >= thr).astype(int)
        report = classification_report(
            val_labels, preds,
            target_names=["adulto(0)", "menor(1)"],
            output_dict=True,
        )
        cm = confusion_matrix(val_labels, preds)
        tn, fp, fn, tp = cm.ravel()

        minor_recall = report["menor(1)"]["recall"]
        minor_prec   = report["menor(1)"]["precision"]

        print(f"\n── Threshold {thr} ────────────────────────────────────")
        print(classification_report(val_labels, preds, target_names=["adulto(0)", "menor(1)"]))
        print(f"  Menores detectados : {tp}/{tp+fn}  ({minor_recall*100:.1f}% recall)")
        print(f"  Adultos bloqueados : {fp}/{tn+fp}  (falsos positivos)")
        print(f"  Matriz: TN={tn}  FP={fp}  FN={fn}  TP={tp}")

        if minor_prec >= 0.60 and minor_recall > best_recall:
            best_recall = minor_recall
            best_thr    = thr

    print(f"\n{'='*55}")
    print(f"  Threshold recomendado : {best_thr}")
    print(f"  Recall menores        : {best_recall*100:.1f}%")
    print(f"  → Ajusta MINOR_PROB_THRESHOLD={best_thr} en docker-compose.yml")
    print(f"{'='*55}\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--model",   default=str(MODEL_PATH), help="Ruta al .keras")
    parser.add_argument("--dataset", default=str(DATASET_DIR), help="Ruta al dataset")
    parser.add_argument("--samples", type=int, default=None,
                        help="Límite de imágenes para eval rápida (ej. 2000)")
    args = parser.parse_args()

    evaluate(Path(args.model), Path(args.dataset), args.samples)
