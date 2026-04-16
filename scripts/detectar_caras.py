"""
Detección de caras en una imagen local (OpenCV Haar Cascade).
No requiere ningún modelo externo.

Uso:
    python detectar_caras.py <ruta_imagen> [--mostrar]
"""
import argparse
import cv2
import numpy as np


def _filtrar_outliers(rects, alto):
    """Elimina detecciones que están muy lejos del grupo principal (outliers de posición)."""
    if len(rects) <= 2:
        return rects

    centros_y = np.array([y + h // 2 for (x, y, w, h) in rects], dtype=float)
    mediana_y = np.median(centros_y)
    mad_y     = np.median(np.abs(centros_y - mediana_y))  # desviación absoluta mediana
    umbral    = max(mad_y * 3.0, alto * 0.12)             # al menos 12% de la imagen

    return [r for r, cy in zip(rects, centros_y) if abs(cy - mediana_y) <= umbral]


def _nms(rects, umbral_iou=0.4):
    """Elimina rectángulos solapados (non-maximum suppression)."""
    if len(rects) == 0:
        return []
    rects = sorted(rects, key=lambda r: r[2] * r[3], reverse=True)
    seleccionados = []
    for x1, y1, w1, h1 in rects:
        solapado = False
        for x2, y2, w2, h2 in seleccionados:
            ix = max(0, min(x1+w1, x2+w2) - max(x1, x2))
            iy = max(0, min(y1+h1, y2+h2) - max(y1, y2))
            inter = ix * iy
            union = w1*h1 + w2*h2 - inter
            if union > 0 and inter / union > umbral_iou:
                solapado = True
                break
        if not solapado:
            seleccionados.append((x1, y1, w1, h1))
    return seleccionados


def detectar_caras(ruta_imagen: str):
    img = cv2.imread(ruta_imagen)
    if img is None:
        raise ValueError(f'No se pudo leer la imagen: {ruta_imagen}')

    alto, ancho = img.shape[:2]
    min_lado = max(40, int(min(alto, ancho) * 0.04))

    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)

    # default → caras frontales | alt2 → caras con ángulo/giro
    cascades = [
        cv2.CascadeClassifier(cv2.data.haarcascades + 'haarcascade_frontalface_default.xml'),
        cv2.CascadeClassifier(cv2.data.haarcascades + 'haarcascade_frontalface_alt2.xml'),
    ]

    todas = []
    for cascade in cascades:
        rects = cascade.detectMultiScale(
            gray,
            scaleFactor=1.05,
            minNeighbors=5,
            minSize=(min_lado, min_lado),
        )
        if len(rects) > 0:
            todas.extend(rects.tolist())

    # 1. Eliminar duplicados entre los dos cascades
    todas = _nms(todas, umbral_iou=0.4)
    # 2. Eliminar detecciones aisladas fuera del grupo (sillas, techo…)
    todas = _filtrar_outliers(todas, alto)

    caras = [
        {'num_cara': i + 1, 'x': int(x), 'y': int(y), 'w': int(w), 'h': int(h)}
        for i, (x, y, w, h) in enumerate(rects)
    ]
    return img, caras


def dibujar_caras(img, caras: list[dict]):
    img_out = img.copy()
    for cara in caras:
        x, y, w, h = cara['x'], cara['y'], cara['w'], cara['h']
        cv2.rectangle(img_out, (x, y), (x + w, y + h), (0, 255, 0), 2)
        cv2.putText(img_out, f'Cara {cara["num_cara"]}', (x, y - 8),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 0), 2)
    return img_out


def main():
    parser = argparse.ArgumentParser(description='Detecta caras en una imagen')
    parser.add_argument('imagen', help='Ruta a la imagen')
    parser.add_argument('--mostrar', action='store_true',
                        help='Muestra la imagen con las caras marcadas')
    args = parser.parse_args()

    img, caras = detectar_caras(args.imagen)

    print(f'\nImagen: {args.imagen}')
    print(f'Caras detectadas: {len(caras)}')
    for c in caras:
        print(f'  Cara {c["num_cara"]}: x={c["x"]}, y={c["y"]}, w={c["w"]}, h={c["h"]}')

    img_marcada = dibujar_caras(img, caras)

    partes = args.imagen.rsplit('.', 1)
    salida = f'{partes[0]}_caras.{partes[1]}' if len(partes) == 2 else f'{partes[0]}_caras.jpg'
    cv2.imwrite(salida, img_marcada)
    print(f'Imagen guardada: {salida}')

    if args.mostrar:
        cv2.imshow('Detección de caras', img_marcada)
        print('Presiona cualquier tecla para cerrar...')
        cv2.waitKey(0)
        cv2.destroyAllWindows()


if __name__ == '__main__':
    main()
