# 🎬 FletStream Pro

![Version](https://img.shields.io/badge/version-1.5.0-red)
![Python](https://img.shields.io/badge/python-3.9+-blue.svg)
![Flet](https://img.shields.io/badge/Flet-0.80.1-cyan)

Una aplicación de streaming, descarga y gestión de contenido multimedia moderna, desarrollada con **Flet** (Python). Diseñada con una interfaz oscura estilo Netflix, enfocada en contenido en español para Latinoamérica.

Cuenta con un potente **gestor de descargas** con interfaz visual, un **extractor de enlaces robusto** para servidores VOE, y un **sistema de scraping integrado** para mantener el catálogo actualizado automáticamente.

## ✨ Características Principales

*   **📺 Catálogo Completo:** Soporte nativo para **Películas, Series, Animes y Doramas** con navegación lateral intuitiva.
*   **🔄 Auto-Actualizador (Scraper):**
    *   Botón integrado para actualizar la base de datos desde `pelisplushd.bz` sin salir de la app.
    *   Procesamiento concurrente con `ThreadPoolExecutor` para descarga rápida de metadatos.
    *   Desencriptación de enlaces en tiempo real durante el scraping.
*   **📥 Gestor de Descargas Avanzado:**
    *   Sistema de cola con hasta **2 descargas simultáneas**.
    *   **Interfaz de Tarjetas:** Visualización gráfica de cada descarga con barras de progreso en tiempo real y botón de cancelación.
    *   Historial de descargas persistente en JSON.
    *   Soporte para rutas Android (`/sdcard/Download/`) y Escritorio.
*   **🔍 Extractor Robusto (VOE):**
    *   Motor de extracción de enlaces que utiliza múltiples técnicas (Regex, BeautifulSoup, deofuscación ROT13/Base64, manipulación de DOM) para obtener enlaces directos `.mp4` o `.m3u8`.
    *   Detección de redirecciones y manejo de protecciones simples.
*   **🎨 Interfaz Moderna:** Diseño "Dark Mode" responsivo, filtrado por géneros, búsqueda en tiempo real y caché de pósters.
*   **📝 Logs y Depuración:** Visualización de logs del sistema y historial de descargas dentro de la aplicación.

## 🛠️ Stack Tecnológico

*   **Frontend:** [Flet](https://flet.dev/) (Framework Flutter para Python).
*   **Video:** [flet-video](https://github.com/flet-dev/flet-video).
*   **Scraping:** [Requests](https://requests.readthedocs.io/), [BeautifulSoup4](https://www.crummy.com/software/BeautifulSoup/bs4/doc/).
*   **Seguridad/Encriptación:** `pycryptodome` (para AES y desencriptado de enlaces).
*   **Concurrencia:** `threading`, `asyncio`, `concurrent.futures`.

## 📦 Instalación y Ejecución

### Prerrequisitos

*   Python 3.9 o superior.
*   Pip (gestor de paquetes de Python).

### Pasos

1.  **Clona el repositorio:**
    ```bash
    git clone https://github.com/yaeck04/FletStream-stable.git
    cd FletStream-stable
    ```

2.  **Crea un entorno virtual (Recomendado):**
    ```bash
    python -m venv venv
    # Windows:
    venv\Scripts\activate
    # Linux/Mac:
    source venv/bin/activate
    ```

3.  **Instala las dependencias:**
    Asegúrate de instalar `pycryptodome` necesario para el extractor:
    ```bash
    pip install -r requirements.txt
    # Si requirements.txt falta la dependencia de crypto:
    pip install pycryptodome
    ```

4.  **Ejecuta la aplicación:**
    *La aplicación creará automáticamente las carpetas necesarias (`downloads`, `posters`) y los archivos JSON si no existen.*
    
    ```bash
    python src/main.py
    ```
    *Nota: Al iniciar, puedes usar el botón "Actualizar DB" en el menú lateral para poblar los archivos JSON automáticamente.*

## ⚙️ Formato de Datos (JSON)

La aplicación soporta dos estructuras principales dependiendo del tipo de contenido.

### 1. Estructura para Películas
Archivo: `peliculas_con_reproductores.json` (o `animes.json` para películas de anime).

```json
[
  {
    "titulo": "Nombre de la Película",
    "anio": "2024",
    "poster": "https://ejemplo.com/poster.jpg",
    "genero": ["Acción", "Aventura"],
    "sinopsis": "Descripción breve de la trama...",
    "tipo": "pelicula",
    "url": "https://url-de-referencia.com",
    "reproductores": [
      {
        "servidor": "VOE",
        "idioma": "Latino",
        "url": "https://voe.sx/..."
      }
    ]
  }
]
```

### 2. Estructura para Series, Animes (Serie) y Doramas
Archivo: `series.json`, `doramas.json`.
Esta estructura anida temporadas y episodios.

```json
[
  {
    "titulo": "Nombre de la Serie",
    "anio": "2023",
    "poster": "https://ejemplo.com/poster.jpg",
    "genero": ["Drama"],
    "sinopsis": "Sinopsis de la serie...",
    "tipo": "serie",
    "temporadas": {
      "1": [
        {
          "titulo": "Episodio 1 - Pilot",
          "url": "https://url-del-episodio.com",
          "reproductores": [
            {
              "servidor": "VOE",
              "idioma": "Subtitulado",
              "url": "https://voe.sx/..."
            }
          ]
        }
      ]
    }
  }
]
```

## 🏗️ Compilar para Android (APK)

Este proyecto utiliza **GitHub Actions** para compilar automáticamente la APK.

### Compilación Manual
Si prefieres compilar localmente:
```bash
flet build apk --project src
```

## 📂 Estructura del Proyecto

```text
FletStream/
├── src/
│   └── main.py              # Código principal (Scraper + UI + Descargas)
├── downloads/               # Carpeta donde se guardan los videos (Creada auto)
├── posters/                 # Carpeta de caché de imágenes (Creada auto)
├── peliculas_con_reproductores.json # DB Películas
├── series.json              # DB Series
├── animes.json              # DB Animes
├── doramas.json             # DB Doramas
├── historial_descargas.json # Historial local
├── requirements.txt         # Dependencias
└── README.md               # Esta documentación
```

## 🤝 Contribuir

Las contribuciones son bienvenidas. Si encuentras un bug o tienes una mejora, por favor abre un *Issue* o un *Pull Request*.

## ⚠️ Aviso Legal

Este software es una herramienta de gestión y reproducción. El desarrollador no aloja ningún contenido multimedia. El usuario es responsable del uso que le dé a la aplicación y de respetar las leyes de derechos de autor de su país.

## 📜 Licencia

Este proyecto es de código abierto y está disponible bajo la [Licencia MIT](LICENSE).

---
<div align="center">

Desarrollado con ❤️ usando Python y Flet.

**Dedicado a ⭐ Fernan ⭐**

*Desarrollado por Ing. YaeCk*

</div>