# Superblock-Detector BS

Ein QGIS-Python-Skript zur automatisierten Analyse und Identifikation potenzieller Superblock-Standorte in der Stadt Basel.

## 📋 Inhaltsverzeichnis

- [Installation](#installation)
- [Eingabedaten](#eingabedaten)
- [Verwendung](#verwendung)
- [Funktionsweise](#funktionsweise)
- [Ausgabe](#ausgabe)
- [Datenkompatibilität](#datenkompatibilität)
- [Troubleshooting](#troubleshooting)

## 🚀 Skript starten

1. Öffnen Sie QGIS (getestet mit Version 3.40.5)
2. Navigieren Sie zu "Verarbeitung" → blenden Sie das Menü "Werkzeugkiste" ein (oder Strg + Alt + T)
3. Wählen Sie da Python-Symbol aus → "Vorhandenes Skript öffnen..."
4. Wählen Sie die Datei "superblock-detector-bs.py" in Ihrer Ablage aus
5. Mit klick auf "Öffnen" wird das Skript in QGIS geladen
6. Über den grünen Playbutton kann das Skript gestartet werden

## ⚙️ Skript in Werkzeugkasten hinzufügen (optional)

1. Öffnen Sie QGIS (getestet mit Version 3.40.5)
2. Navigieren Sie zu "Verarbeitung" → blenden Sie das Menü "Werkzeugkiste" ein (oder Strg + Alt + T)
3. Wählen Sie da Python-Symbol aus → "Skript zu Werkzeugkasten hinzufügen..."
4. Wählen Sie die Datei "superblock-detector-bs.py" in Ihrer Ablage aus
5. Mit klick auf "Öffnen" wird das Skript in QGIS geladen
6. Über die Suchfunktion (Skripte) in den Verarbeitungswerkzeugen kann nun das Skript direkt aufgerufen werden

## 📥 Eingabedaten

Das Tool benötigt folgende Eingabedaten in einem geeigneten Format (GPKG-, oder SHP-Format):

1. **Mobilitätsnetz (Strassennetz) (Linien)**

   - Muss das Feld "Strassennetzhierarchie" enthalten
   - Wird nach "QSS" und "ES" gefiltert

2. **Liniennetz öffentlicher Verkehr (Linien)**

   - Routen des öffentlichen Verkehrs
   - Wird für Ausschlussflächen verwendet

3. **Teilrichtplan Velo (Linien)**

   - Velorouten
   - Wird für Ausschlussflächen verwendet

4. **Ausnahmetransportrouten (Linien)**

   - Routen für Ausnahmetransporte
   - Wird für Ausschlussflächen verwendet

5. **Lifeline- und Notfallachsen (Linien)**

   - Wichtige Verkehrsachsen
   - Wird für Ausschlussflächen verwendet

6. **Gebäudeinformationen (Punkte)**

   - Entweder GWR-Daten (Bund) oder kantonale Daten
   - GWR: Muss Feld "GKLAS" enthalten
   - Kantonal: Muss Feld "GEBKATEGO" enthalten

7. **Liegenschaftsflächen (Polygone)**
   - Grundstücksflächen der Amtlichen Vermessung
   - Dient als Basis für die Blockidentifikation

## 🎯 Verwendung

1. **Vorbereitung**

   - Stellen Sie sicher, dass alle Eingabedaten im geeigneten Format vorliegen
   - Überprüfen Sie die erforderlichen Felder in den Eingabelayern (sind die richtigen Daten am richtigen Ort)

2. **Tool starten**

   - Öffnen Sie die QGIS Verarbeitungswerkzeuge
   - Navigieren Sie zu "Superblock-Analyse" → "Superblock-Detector BS"
   - Wählen Sie die Eingabelayer aus
   - Definieren Sie ein Zielverzeichnis für die Ergebnisse
   - Wählen Sie die gewünschte Score-Gewichtung

3. **Score-Gewichtung**
   - Standard: Gebäudescore 80% / Verhältnis-Score 20%
   - Alternative Gewichtungen verfügbar:
     - 70/30: Mehr Fokus auf Blockform
     - 60/40: Ausgewogene Bewertung
     - 50/50: Gleichgewichtung
     - 40/60: Starker Fokus auf Blockform
     - 30/70: Sehr starker Fokus auf Blockform
     - 20/80: Extrem starker Fokus auf Blockform

## 🔄 Funktionsweise

Das Tool durchläuft vier Hauptphasen:

### Phase 1: Vorprozessierung

- Transformation aller Layer in EPSG:2056 (LV95)
- Filterung und Aufbereitung der Gebäudedaten
- Export der vorbereiteten Layer

### Phase 2: Bereinigung und Segmentierung

- Filterung des Mobilitätsnetzes nach Hierarchie
- Erstellung von Ausschlussflächen (15m Puffer)
- Bereinigung des Netzes
- Segmentierung in Komponenten
- Identifikation von Stützpunkten
- Erstellung eines finalen Netzbuffers

### Phase 3: Geobasierte Analyse

- Erstellung eines negativen Buffers um Ausschlussflächen
- Extraktion von Liegenschaften
- Filterung nach Gebäudebestand
- Verschmelzung und Teilung der Flächen
- Extraktion geeigneter Blocks

### Phase 4: Quantilbasierte Bewertung

- Aggregation der Gebäudescores
- Quantilskalierung der Gebäudebewertung
- Berechnung der Verhältnisbewertung
- Gewichtete Zusammenführung der Scores
- Erstellung der finalen Bewertung

## 📤 Ausgabe

Das Tool erstellt einen strukturierten Projektordner mit folgenden Unterordnern:

- **\_prepared_inputdata/**

  - Transformierte und gefilterte Eingabelayer
  - Vorbereitete Gebäudedaten

- **\_tempdata/**

  - Temporäre Zwischenergebnisse
  - Wird nach erfolgreicher Ausführung bereinigt

- **\_finaloutput/**

  - Bereinigtes Mobilitätsnetz
  - Finale Bewertung der Blocks
  - Enthält die wichtigsten Ergebnislayer

- **prozess_log.txt**
  - Detailliertes Log der Verarbeitung
  - Enthält Informationen zu jedem Verarbeitungsschritt

## 🔄 Datenkompatibilität

Das Tool wurde speziell für die Datenstruktur des Kantons Basel-Stadt entwickelt und getestet. Die Verwendung mit anderen Datensätzen ist theoretisch möglich, wurde jedoch nicht validiert. Bei der Anwendung auf andere Datenquellen müssen möglicherweise folgende Anpassungen vorgenommen werden:

- Feldnamen und -strukturen in den Filterausdrücken
- Gebäudeklassifikationen und deren Bewertung
- Schwellenwerte für die Verhältnisbewertung
- Buffer-Distanzen für die Netzbereinigung

## ⚠️ Troubleshooting

### Häufige Fehler und Lösungen

1. **Layer nicht gefunden**

   - Überprüfen Sie die Pfade zu den Eingabelayern
   - Stellen Sie sicher, dass alle Layer im geeigneten Format vorliegen

2. **Fehlende Felder**

   - Überprüfen Sie die erforderlichen Felder in den Eingabelayern
   - Konsultieren Sie die Dokumentation der Eingabedaten

3. **Prozess bricht ab**
   - Überprüfen Sie das integrierte Protokoll für detaillierte Fehlermeldungen
   - Stellen Sie sicher, dass genügend Speicherplatz verfügbar ist

### Support

Bei Fragen oder Problemen:

1. Konsultieren Sie die prozess_log.txt
2. Überprüfen Sie die Eingabedaten

## 📝 Lizenz

GNU General Public License v2 oder höher

## 👤 Entwickler

- **Name:** Nathan Matzinger
- **Kontext:** Bachelorarbeit "Multikriterielle Datenanalyse Superblock"
- **Institution:** Fachhochschule Nordwestschweiz, Institut Geomatik
- **Entwicklung:** 2024-2025
