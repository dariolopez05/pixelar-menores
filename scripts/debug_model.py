#!/usr/bin/env python3
"""
Diagnóstico rápido del modelo: muestra las probabilidades RAW para imágenes
conocidas del dataset. Si el modelo está colapsado, verás todos los valores
cerca de 0 (siempre adulto) o cerca de 1 (siempre menor).

Uso:
    python scripts/debug_model.py
"""
import os, sys
from pathlib import Path

import cv2
import numpy as np

os.environ["TF_CPP_MIN_LOG_LEVEL"] = "2"
import tensorflow as tf

ROOT       = Path(__file__).parent.parent
MODEL_PATH = ROOT / "age-detection" / "model" / "age_classifier.keras"
DATASET    = ROOT / "dataset"
IMG_SIZE   = (200, 200)

AGES_TO_TEST = [1, 5, 10, 14, 16, 17, 18, 20, 25, 35, 50]

def preprocess_tf(path: Path):
    """Mismo pipeline que el training."""
    img = tf.io.read_file(str(path))
    img = tf.image.decode_png(img, channels=3)
    img = tf.image.resize(img, IMG_SIZE)
    img = tf.keras.applications.mobilenet_v2.preprocess_input(img)
    return img.numpy()[np.newaxis]

def preprocess_cv2(path: Path):
    """Mismo pipeline que age-detection/main.py en producción."""
    img = cv2.imdecode(np.frombuffer(path.read_bytes(), np.uint8), cv2.IMREAD_COLOR)
    img = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
    img = cv2.resize(img, IMG_SIZE)
    img = img.astype(np.float32)
    img = tf.keras.applications.mobilenet_v2.preprocess_input(img)
    return img[np.newaxis]

def main():
    if not MODEL_PATH.exists():
        sys.exit(f"No se encuentra {MODEL_PATH}")

    print(f"Cargando modelo desde {MODEL_PATH}...")
    model = tf.keras.models.load_model(MODEL_PATH)
    print(f"Input shape esperado: {model.input_shape}\n")

    print(f"{'Edad':>5} {'Label':>8} {'Prob(TF)':>10} {'Prob(CV2)':>10} {'Δ':>8} {'OK?':>6}")
    print("-" * 55)

    all_probs = []

    for age in AGES_TO_TEST:
        folder = DATASET / str(age).zfill(3)
        if not folder.exists():
            folder = DATASET / str(age)
        if not folder.exists():
            print(f"{age:>5}  carpeta no encontrada")
            continue

        imgs = list(folder.glob("*.png"))[:5]
        if not imgs:
            continue

        expected = "MENOR" if age < 18 else "adulto"

        for img_path in imgs:
            try:
                inp_tf  = preprocess_tf(img_path)
                inp_cv2 = preprocess_cv2(img_path)

                prob_tf  = float(model.predict(inp_tf,  verbose=0)[0][0])
                prob_cv2 = float(model.predict(inp_cv2, verbose=0)[0][0])
                delta    = abs(prob_tf - prob_cv2)
                pred     = "MENOR" if prob_cv2 >= 0.5 else "adulto"
                ok       = "✓" if pred == expected else "✗"

                print(f"{age:>5} {expected:>8} {prob_tf:>10.4f} {prob_cv2:>10.4f} {delta:>8.4f} {ok:>6}")
                all_probs.append(prob_cv2)
            except Exception as e:
                print(f"{age:>5}  ERROR: {e}")

    if all_probs:
        print("\n── Estadísticas de probabilidades (vía CV2) ──")
        arr = np.array(all_probs)
        print(f"  Min   : {arr.min():.4f}")
        print(f"  Max   : {arr.max():.4f}")
        print(f"  Media : {arr.mean():.4f}")
        print(f"  Std   : {arr.std():.4f}")
        print()
        if arr.max() - arr.min() < 0.1:
            print("  ⚠ MODELO COLAPSADO: todas las probabilidades son casi iguales.")
            print("    El modelo no está discriminando. Necesita reentrenamiento.")
        elif arr.mean() > 0.75:
            print("  ⚠ SESGO HACIA MENOR: el modelo predice 'menor' casi siempre.")
            print("    Reduce CLASS_WEIGHT_MINOR o baja el threshold.")
        elif arr.mean() < 0.25:
            print("  ⚠ SESGO HACIA ADULTO: el modelo predice 'adulto' casi siempre.")
            print("    Aumenta CLASS_WEIGHT_MINOR o sube el threshold.")
        else:
            print("  ✓ Las probabilidades tienen rango razonable.")

if __name__ == "__main__":
    main()
