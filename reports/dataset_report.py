#!/usr/bin/env python3
"""
Genera un reporte completo del dataset de entrenamiento.
No requiere TensorFlow ni scikit-learn; solo stdlib + matplotlib (opcional).
Salida: reports/dataset_analysis.json  y  reports/dataset_analysis.txt
        reports/dataset_distribution.png  (si matplotlib está disponible)
"""

import json
import struct
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path

REPORTS_DIR = Path(__file__).parent
DATASET_DIR = REPORTS_DIR.parent / "dataset"
MINOR_THRESHOLD = 18


# ── Lectura del dataset ────────────────────────────────────────────────────────

def png_size(path: Path):
    """Lee ancho/alto de un PNG sin librerías externas."""
    try:
        with open(path, "rb") as f:
            if f.read(8) != b"\x89PNG\r\n\x1a\n":
                return None
            f.read(4); f.read(4)
            w = struct.unpack(">I", f.read(4))[0]
            h = struct.unpack(">I", f.read(4))[0]
            return w, h
    except Exception:
        return None


def collect_stats():
    ages = {}
    for age_dir in sorted(DATASET_DIR.iterdir()):
        if not age_dir.is_dir():
            continue
        age = int(age_dir.name.lstrip("0") or "0")
        images = list(age_dir.glob("*.png"))
        sizes = [png_size(p) for p in images[:10]]
        sizes = [s for s in sizes if s]
        ages[age] = {"count": len(images), "sizes": sizes}
    return ages


# ── Análisis ───────────────────────────────────────────────────────────────────

def analyse(ages: dict):
    minors = {a: v for a, v in ages.items() if a < MINOR_THRESHOLD}
    adults = {a: v for a, v in ages.items() if a >= MINOR_THRESHOLD}

    total          = sum(v["count"] for v in ages.values())
    total_minors   = sum(v["count"] for v in minors.values())
    total_adults   = sum(v["count"] for v in adults.values())
    ratio          = total_minors / total_adults if total_adults else 0

    all_sizes = []
    for v in ages.values():
        all_sizes.extend(v["sizes"])
    unique_sizes = list(set(all_sizes))
    widths  = [s[0] for s in all_sizes]
    heights = [s[1] for s in all_sizes]

    # Edades con pocos datos
    sparse       = {a: v["count"] for a, v in ages.items() if v["count"] < 50}
    sparse_minor = {a: c for a, c in sparse.items() if a < MINOR_THRESHOLD}
    sparse_adult = {a: c for a, c in sparse.items() if a >= MINOR_THRESHOLD}

    # Distribución por décadas
    decade_counts = defaultdict(int)
    for age, v in ages.items():
        decade = (age // 10) * 10
        decade_counts[decade] += v["count"]

    # Concentración: cuánto aportan las N edades más representadas
    sorted_by_count = sorted(ages.items(), key=lambda x: x[1]["count"], reverse=True)
    top5_total = sum(v["count"] for _, v in sorted_by_count[:5])
    top5_pct   = top5_total / total * 100

    # Borderline (17–19)
    borderline = {a: ages[a]["count"] for a in [16, 17, 18, 19, 20] if a in ages}

    return {
        "generated_at":     datetime.now().isoformat(),
        "dataset_path":     str(DATASET_DIR),
        "total_images":     total,
        "total_age_folders": len(ages),
        "age_range":        [min(ages), max(ages)],
        "class_balance": {
            "minor_images":   total_minors,
            "adult_images":   total_adults,
            "minor_pct":      round(total_minors / total * 100, 1),
            "adult_pct":      round(total_adults / total * 100, 1),
            "minor_adult_ratio": round(ratio, 3),
            "minor_age_folders": len(minors),
            "adult_age_folders": len(adults),
        },
        "image_sizes": {
            "unique_resolutions": unique_sizes,
            "all_same_size":      len(unique_sizes) == 1,
            "width_range":        [min(widths), max(widths)] if widths else [],
            "height_range":       [min(heights), max(heights)] if heights else [],
        },
        "distribution_by_decade": dict(sorted(decade_counts.items())),
        "borderline_ages_16_20":  borderline,
        "sparse_ages": {
            "threshold":           50,
            "total_sparse_ages":   len(sparse),
            "sparse_minor_ages":   sparse_minor,
            "sparse_adult_ages":   dict(list(sparse_adult.items())[:15]),
        },
        "concentration": {
            "top5_ages":           [(a, v["count"]) for a, v in sorted_by_count[:5]],
            "top5_pct_of_total":   round(top5_pct, 1),
            "age1_pct_of_minors":  round(ages.get(1, {}).get("count", 0) / total_minors * 100, 1),
        },
        "per_age": {a: v["count"] for a, v in sorted(ages.items())},
    }


# ── Texto del reporte ──────────────────────────────────────────────────────────

def build_text_report(stats: dict) -> str:
    cb  = stats["class_balance"]
    img = stats["image_sizes"]
    con = stats["concentration"]
    sp  = stats["sparse_ages"]
    bl  = stats["borderline_ages_16_20"]

    lines = [
        "=" * 65,
        "  REPORTE DE DATASET — CLASIFICADOR MENOR/ADULTO",
        f"  Generado: {stats['generated_at'][:19]}",
        "=" * 65,
        "",
        "── VISIÓN GENERAL ──────────────────────────────────────────",
        f"  Imágenes totales     : {stats['total_images']:,}",
        f"  Carpetas de edad     : {stats['total_age_folders']}",
        f"  Rango de edades      : {stats['age_range'][0]}–{stats['age_range'][1]} años",
        "",
        "── BALANCE DE CLASES ───────────────────────────────────────",
        f"  Menores (<18)  : {cb['minor_images']:,} imgs  ({cb['minor_pct']}%)  en {cb['minor_age_folders']} edades",
        f"  Adultos (≥18)  : {cb['adult_images']:,} imgs  ({cb['adult_pct']}%)  en {cb['adult_age_folders']} edades",
        f"  Ratio M/A      : {cb['minor_adult_ratio']}  {'⚠  desbalance moderado' if cb['minor_adult_ratio'] < 0.75 else 'OK'}",
        "",
        "── RESOLUCIÓN DE IMÁGENES ──────────────────────────────────",
    ]

    if img["all_same_size"]:
        w, h = img["unique_resolutions"][0]
        lines.append(f"  Todas las imágenes son {w}×{h} px  ✓")
    else:
        lines.append(f"  Resoluciones distintas: {img['unique_resolutions']}")
        lines.append(f"  Ancho: {img['width_range'][0]}–{img['width_range'][1]} px")
        lines.append(f"  Alto:  {img['height_range'][0]}–{img['height_range'][1]} px")

    lines += [
        "",
        "── CONCENTRACIÓN DE DATOS ──────────────────────────────────",
        f"  Top-5 edades aportan el {con['top5_pct_of_total']}% del total:",
    ]
    for age, cnt in con["top5_ages"]:
        label = "MENOR" if age < MINOR_THRESHOLD else "adulto"
        bar = "█" * (cnt // 40)
        lines.append(f"    Edad {age:3d} ({label}): {cnt:4d}  {bar}")

    lines += [
        f"  Edad 1 representa el {con['age1_pct_of_minors']}% de todos los menores",
        "",
        "── EDADES LIMÍTROFES (16–20) ───────────────────────────────",
    ]
    for age in sorted(bl):
        label = "MENOR ←" if age < MINOR_THRESHOLD else "→ adulto"
        lines.append(f"  Edad {age}: {bl[age]:4d} imgs  [{label}]")

    lines += [
        "",
        "── EDADES CON POCOS DATOS (< 50 imgs) ─────────────────────",
        f"  Total edades afectadas: {sp['total_sparse_ages']}",
        f"  Menores escasos      : {list(sp['sparse_minor_ages'].keys()) or 'ninguno'}",
        f"  Adultos escasos (top): {list(sp['sparse_adult_ages'].keys())}",
        "",
        "── DISTRIBUCIÓN POR DÉCADAS ────────────────────────────────",
    ]
    for decade, cnt in stats["distribution_by_decade"].items():
        label = "MENOR" if decade < MINOR_THRESHOLD else "adulto"
        bar = "█" * (cnt // 60)
        lines.append(f"  {decade:3d}s ({label}): {cnt:5d}  {bar}")

    lines += [
        "",
        "── DIAGNÓSTICO Y RECOMENDACIONES ───────────────────────────",
    ]
    lines += build_recommendations(stats)

    lines += ["", "=" * 65]
    return "\n".join(lines)


def build_recommendations(stats: dict) -> list[str]:
    recs = []
    cb  = stats["class_balance"]
    con = stats["concentration"]
    sp  = stats["sparse_ages"]

    # 1. Desequilibrio de clases
    ratio = cb["minor_adult_ratio"]
    if ratio < 0.75:
        recs.append(f"  [WARN] Ratio menor/adulto = {ratio}. El modelo tenderá a predecir")
        recs.append(f"         'adulto' más fácilmente. Mitigación: class_weight='balanced'")
        recs.append(f"         (ya incluido en el script de entrenamiento).")

    # 2. Dominancia de edad 1
    age1_pct = con["age1_pct_of_minors"]
    if age1_pct > 20:
        recs.append(f"")
        recs.append(f"  [CRIT] Edad 1 = {age1_pct}% de todos los menores.")
        recs.append(f"         Riesgo: el modelo aprende 'cara de bebé' como proxy de menor.")
        recs.append(f"         Recomendación: limitar edad 1 a max ~200 imgs antes de entrenar")
        recs.append(f"         (añade 'MAX_IMAGES_PER_AGE = 200' en el script de entrenamiento).")

    # 3. Zonas escasas en adultos
    n_sparse_adult = len(sp["sparse_adult_ages"])
    if n_sparse_adult > 10:
        recs.append(f"")
        recs.append(f"  [WARN] {n_sparse_adult} edades adultas con menos de 50 imgs (60–110 años).")
        recs.append(f"         El modelo generalizará mal con personas mayores.")
        recs.append(f"         Si el sistema procesa vídeos con ancianos, considera ampliar el dataset.")

    # 4. Zona limítrofe 17-18
    bl = stats["borderline_ages_16_20"]
    age17 = bl.get(17, 0)
    age18 = bl.get(18, 0)
    recs.append(f"")
    recs.append(f"  [INFO] Zona limítrofe: edad 17={age17} imgs, edad 18={age18} imgs.")
    recs.append(f"         La frontera 17/18 es la más crítica. Ambas tienen datos suficientes.")
    recs.append(f"         Si el recall en frontera es bajo, prueba MINOR_PROB_THRESHOLD=0.4.")

    # 5. Tamaño de imagen
    img = stats["image_sizes"]
    if img["all_same_size"]:
        w, h = img["unique_resolutions"][0]
        recs.append(f"")
        recs.append(f"  [INFO] Todas las imágenes son {w}×{h} px (uniforme, ideal).")
        recs.append(f"         El script entrena con IMG_SIZE=({w},{h}) para no perder información.")

    if not recs:
        recs.append("  [OK] Dataset en buen estado para el entrenamiento.")

    return recs


# ── Gráfico (opcional) ─────────────────────────────────────────────────────────

def plot_distribution(ages: dict):
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        print("  matplotlib no disponible — gráfico omitido.")
        return

    sorted_ages = sorted(ages.items())
    age_list    = [a for a, _ in sorted_ages]
    counts      = [v["count"] for _, v in sorted_ages]
    colors      = ["#e74c3c" if a < MINOR_THRESHOLD else "#3498db" for a in age_list]

    fig, axes = plt.subplots(1, 2, figsize=(16, 5))

    # Barras por edad
    axes[0].bar(age_list, counts, color=colors, width=0.8)
    axes[0].axvline(MINOR_THRESHOLD - 0.5, color="black", linewidth=2, linestyle="--", label="Umbral 18")
    axes[0].set_title("Imágenes por edad", fontsize=13)
    axes[0].set_xlabel("Edad")
    axes[0].set_ylabel("Nº imágenes")
    axes[0].legend()
    from matplotlib.patches import Patch
    axes[0].legend(handles=[
        Patch(color="#e74c3c", label=f"Menor (<18)  {sum(v['count'] for a,v in sorted_ages if a < 18):,}"),
        Patch(color="#3498db", label=f"Adulto (≥18)  {sum(v['count'] for a,v in sorted_ages if a >= 18):,}"),
    ])

    # Pie
    total_m = sum(v["count"] for a, v in sorted_ages if a < MINOR_THRESHOLD)
    total_a = sum(v["count"] for a, v in sorted_ages if a >= MINOR_THRESHOLD)
    axes[1].pie(
        [total_m, total_a],
        labels=[f"Menores\n{total_m:,}", f"Adultos\n{total_a:,}"],
        colors=["#e74c3c", "#3498db"],
        autopct="%1.1f%%",
        startangle=90,
        textprops={"fontsize": 12},
    )
    axes[1].set_title("Balance de clases", fontsize=13)

    fig.tight_layout()
    out = REPORTS_DIR / "dataset_distribution.png"
    fig.savefig(out, dpi=130)
    plt.close(fig)
    print(f"  Grafico guardado: {out}")


# ── Main ───────────────────────────────────────────────────────────────────────

def main():
    print(f"Analizando dataset en: {DATASET_DIR}")
    ages  = collect_stats()
    stats = analyse(ages)

    REPORTS_DIR.mkdir(parents=True, exist_ok=True)

    json_out = REPORTS_DIR / "dataset_analysis.json"
    json_out.write_text(json.dumps(stats, indent=2, ensure_ascii=False))
    print(f"  JSON guardado: {json_out}")

    txt_out = REPORTS_DIR / "dataset_analysis.txt"
    report  = build_text_report(stats)
    txt_out.write_text(report, encoding="utf-8")
    print(f"  TXT  guardado: {txt_out}")

    # Imprimir en terminal con encoding seguro para Windows
    sys.stdout.buffer.write(("\n" + report + "\n").encode("utf-8", errors="replace"))
    sys.stdout.buffer.flush()

    plot_distribution(ages)


if __name__ == "__main__":
    main()
