"""
***************************************************************************
*                                                                         *
*   This program is free software; you can redistribute it and/or modify  *
*   it under the terms of the GNU General Public License as published by  *
*   the Free Software Foundation; either version 2 of the License, or     *
*   (at your option) any later version.                                   *
*                                                                         *
***************************************************************************

Superblock-Analyse Tool für QGIS
--------------------------------

Entwickelt von: Nathan Matzinger
Entwicklung: 2024-2025
Bachelorarbeit: "Multikriterielle Datenanalyse Superblock"
Fachhochschule Nordwestschweiz, Institut Geomatik

Beschreibung:
------------
Dieses QGIS-Python-Skript ermöglicht die automatisierte Analyse und Identifikation
potenzieller Superblock-Standorte in der Stadt Basel. Das Tool verarbeitet
diverse Eingabedaten (Gebäude, Mobilitätsnetz, etc.), bereinigt diese und
berechnet einen gewichteten Score für mögliche Standorte.

Datenkompatibilität:
------------------
Dieses Skript wurde speziell für die Datenstruktur des Kantons Basel-Stadt
entwickelt und getestet. Die Verwendung mit anderen Datensätzen ist theoretisch
möglich, wurde jedoch nicht validiert. Bei der Anwendung auf andere Datenquellen
müssen möglicherweise folgende Anpassungen vorgenommen werden:
- Feldnamen und -strukturen in den Filterausdrücken
- Gebäudeklassifikationen und deren Bewertung
- Schwellenwerte für die Verhältnisbewertung
- Buffer-Distanzen für die Netzbereinigung

Hauptfunktionen:
--------------
1. Vorprozessierung: Transformation und Filterung der Eingabedaten
2. Bereinigung und Segmentierung: Aufbereitung des Mobilitätsnetzes
3. Geobasierte Analyse: Identifikation geeigneter Untersuchungsgebiete
4. Quantilbasierte Bewertung: Erstellung einer Bewertungsskala

Eingabedaten (getestet mit Kanton Basel-Stadt):
-------------------------------------------
- Mobilitätsnetz (Strassennetz)
- Liniennetz öffentlicher Verkehr
- Teilrichtplan Velo
- Ausnahmetransportrouten
- Lifeline- und Notfallachsen
- Gebäudeinformationen (kantonal oder GWR)
- Liegenschaftsflächen

Ausgabe:
-------
- Bereinigte und transformierte Datensätze
- Bewertete potenzielle Superblock-Standorte
- Detailliertes Prozess-Log

Lizenz:
-------
GNU General Public License v2 oder höher
"""

from qgis import processing
from qgis.core import (
    QgsFeatureSink,
    QgsProcessing,
    QgsProcessingAlgorithm,
    QgsProcessingContext,
    QgsProcessingException,
    QgsProcessingFeedback,
    QgsProcessingParameterFeatureSource,
    QgsProcessingParameterFolderDestination,
    QgsProcessingParameterEnum,
    QgsProcessingParameterDefinition,
    QgsCoordinateReferenceSystem,
    QgsVectorLayer,
    QgsField,
    NULL
)
from PyQt5.QtCore import QVariant
import os
import shutil
import datetime
from typing import Any, Dict, List, Optional, Tuple, Union
import traceback

"""
Superblock-Analyse Tool für QGIS

Dieses Modul implementiert einen QGIS Processing Algorithmus zur Analyse und Vorbereitung
von Daten für die Superblock-Analyse. Es führt folgende Hauptphasen durch:

1. Vorprozessierung: Transformiert und filtert Eingabedaten
2. Bereinigung & Segmentierung: Bereinigt das Mobilitätsnetz
3. Geobasierte Analyse: Identifiziert geeignete Untersuchungsgebiete
4. Quantilbasierte Bewertung: Erstellt eine Bewertungsskala

Konstanten:
    TARGET_CRS (str): Zielkoordinatensystem (EPSG:2056)
    BUFFER_DISTANCES (dict): Buffer-Distanzen für verschiedene Operationen
    SCORE_THRESHOLDS (dict): Schwellenwerte für verschiedene Bewertungen
    MAX_FOLDER_ATTEMPTS (int): Maximale Anzahl Versuche für Ordnererstellung
"""

TARGET_CRS = "EPSG:2056"

# Buffer-Distanzen in Metern
BUFFER_DISTANCES = {
    "ausschluss": 10,      # Puffer um Ausschlussflächen
    "netz": 15,           # Puffer um Mobilitätsnetz
    "negativ": -8,        # Negativer Puffer für Ausschluss
    "final": 10           # Finaler Netz-Puffer
}

# Schwellenwerte für Bewertungen
SCORE_THRESHOLDS = {
    "stuetzpunkte": 2,    # Minimale Anzahl Verbindungen für Stützpunkte
    "verhaeltnis": {      # Schwellenwerte für Verhältnisbewertung
        85.7: 3,
        71.4: 2,
        57.1: 1,
        42.8: 0,
        28.5: -1,
        14.2: -2
    }
}

MAX_FOLDER_ATTEMPTS = 1000

def reproject_if_needed(layer: QgsVectorLayer, context: QgsProcessingContext, 
                       feedback: QgsProcessingFeedback, log_path: str) -> QgsVectorLayer:
    """
    Reprojiziert einen Layer in das Zielkoordinatensystem, falls nötig.
    
    Args:
        layer: Der zu reprojizierende Layer
        context: QGIS Processing Kontext
        feedback: Feedback-Objekt für Fortschrittsmeldungen
        log_path: Pfad zur Logdatei
        
    Returns:
        QgsVectorLayer: Der reprojizierte oder unveränderte Layer
        
    Raises:
        QgsProcessingException: Bei Fehlern während der Reprojektion
    """
    if not layer.isValid():
        raise QgsProcessingException(f"Ungültiger Layer: {layer.name()}")
        
    source_crs = layer.sourceCrs()
    if source_crs.authid() != TARGET_CRS:
        feedback.pushInfo(f"🔄 Reprojektion nötig: {source_crs.authid()} ➝ {TARGET_CRS}")
        write_log_message(log_path, f"🔄 Reprojektion: {source_crs.authid()} ➝ {TARGET_CRS}")
        try:
            result = processing.run(
                "native:reprojectlayer",
                {
                    "INPUT": layer,
                    "TARGET_CRS": QgsCoordinateReferenceSystem(TARGET_CRS),
                    "OUTPUT": "memory:"
                },
                context=context,
                feedback=feedback
            )
            return result["OUTPUT"]
        except Exception as e:
            raise QgsProcessingException(f"Reprojektion fehlgeschlagen: {str(e)}")
    else:
        feedback.pushInfo(f"✅ Layer bereits in {TARGET_CRS}")
        write_log_message(log_path, f"✅ CRS ok: {source_crs.authid()}")
        return layer

def create_unique_project_folder(base_path: str, feedback: QgsProcessingFeedback) -> str:
    """
    Erstellt einen eindeutigen Projektordner mit Unterordnern.
    
    Args:
        base_path: Basisverzeichnis für den Projektordner
        feedback: Feedback-Objekt für Fortschrittsmeldungen
        
    Returns:
        str: Pfad zum erstellten Projektordner
        
    Raises:
        QgsProcessingException: Bei Fehlern während der Ordnererstellung
    """
    if not os.path.exists(base_path):
        raise QgsProcessingException(f"Basisverzeichnis existiert nicht: {base_path}")
        
    index = 1
    max_attempts = MAX_FOLDER_ATTEMPTS
    
    while index <= max_attempts:
        folder_name = f"_basisdata_superblock_{index}"
        full_path = os.path.join(base_path, folder_name)
        
        try:
            if not os.path.exists(full_path):
                os.makedirs(full_path)
                SUBFOLDERS = ["_prepared_inputdata", "_tempdata", "_finaloutput"]
                for sub in SUBFOLDERS:
                    os.makedirs(os.path.join(full_path, sub))
                feedback.pushInfo(f"✅ Projektordner erstellt: {full_path}")
                return full_path
            index += 1
        except Exception as e:
            raise QgsProcessingException(f"Ordnererstellung fehlgeschlagen: {str(e)}")
            
    raise QgsProcessingException("Maximale Anzahl an Versuchen erreicht")

def write_log_message(log_path: str, message: str) -> None:
    """
    Schreibt eine Nachricht mit Zeitstempel in die Logdatei.
    
    Args:
        log_path: Pfad zur Logdatei
        message: Zu loggende Nachricht
    """
    try:
        timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with open(log_path, "a", encoding="utf-8") as logfile:
            logfile.write(f"[{timestamp}] {message}\n")
    except Exception as e:
        print(f"⚠️ Logging fehlgeschlagen: {e}")

def log_info(feedback: QgsProcessingFeedback, log_path: str, message: str) -> None:
    """
    Kombiniertes Logging für Feedback und Logdatei.
    
    Args:
        feedback: QGIS Processing Feedback-Objekt
        log_path: Pfad zur Logdatei
        message: Zu loggende Nachricht
    """
    feedback.pushInfo(message)
    write_log_message(log_path, message)

def phase_1_vorprozessierung(
    layer_paths: Dict[str, QgsVectorLayer],
    prepared_input_path: str,
    context: QgsProcessingContext,
    feedback: QgsProcessingFeedback,
    log_path: str
) -> None:
    """
    Führt die Vorprozessierung der Eingabelayer durch.
    
    Diese Phase:
    1. Transformiert alle Layer in das Zielkoordinatensystem
    2. Filtert und bereitet Gebäudedaten vor (GWR oder kantonal)
    3. Exportiert die vorbereiteten Layer in GPKG-Format
    """
    if not os.path.exists(prepared_input_path):
        raise QgsProcessingException(f"Zielverzeichnis existiert nicht: {prepared_input_path}")

    log_info(feedback, log_path, "🔄 Starte Phase 1: Vorprozessierung der Eingabelayer")
    log_info(feedback, log_path, f"📂 Zielverzeichnis: {prepared_input_path}")

    export_targets = {
        "mobilitaetsnetz": ("StrassentypWeg", "mobilitaetsnetz_lv95.gpkg"),
        "liniennetz_oev": ("Liniennetz", "liniennetz_oev_lv95.gpkg"),
        "teilrichtplan_velo": ("Velonetz", "trp_velo_lv95.gpkg"),
        "ausnahmetransporte": ("Ausnahmetransportroute", "ausnahmetransportrouten_lv95.gpkg"),
        "notfallachsen": ("Lifeline_Notfallachsen", "lifeline_notfallachsen_lv95.gpkg"),
        "gebaeude": (None, None),  # Wird speziell behandelt
        "liegenschaften": (None, "liegenschaftsflaechen_lv95.gpkg")
    }

    for key, (layername, default_output_name) in export_targets.items():
        if key not in layer_paths or not layer_paths[key]:
            log_info(feedback, log_path, f"⚠️ Layer fehlt oder ungültig: {key}")
            continue

        try:
            layer = layer_paths[key]
            log_info(feedback, log_path, f"📥 Verarbeite Layer: {key}")
            output_name = default_output_name  # Initialisiere output_name mit dem Standardwert
            export_layer = None  # Initialisiere export_layer

            # Gebäude: GWR oder kantonal unterscheiden
            if key == "gebaeude":
                fields = [f.name() for f in layer.fields()]
                log_info(feedback, log_path, f"🔍 Prüfe Gebäudedatenquelle...")

                if "GKLAS" in fields:  # GWR-Daten
                    log_info(feedback, log_path, "📌 Verwende GWR-Gebäudedaten")
                    output_name = "gwr_bund_bs_lv95.gpkg"
                    gefiltert = processing.run("native:extractbyexpression", {
                        'INPUT': layer,
                        'EXPRESSION': '"GSTAT" = 1004',
                        'OUTPUT': 'memory:'
                    }, context=context, feedback=feedback)['OUTPUT']

                    # Gebäudebezeichnungen hinzufügen
                    bezeichnung = processing.run("native:fieldcalculator", {
                        'INPUT': gefiltert,
                        'FIELD_NAME': 'BEZEICHNUNG',
                        'FIELD_TYPE': 2,  # String
                        'FIELD_LENGTH': 100,
                        'NEW_FIELD': True,
                        'FORMULA': _get_gwr_gebaeude_formula(),
                        'OUTPUT': 'memory:'
                    }, context=context, feedback=feedback)['OUTPUT']

                    # Gebäudescores berechnen
                    export_layer = processing.run("native:fieldcalculator", {
                        'INPUT': bezeichnung,
                        'FIELD_NAME': 'score_gebaeude',
                        'FIELD_TYPE': 1,  # Integer
                        'FIELD_LENGTH': 10,
                        'NEW_FIELD': True,
                        'FORMULA': _get_gwr_score_formula(),
                        'OUTPUT': 'memory:'
                    }, context=context, feedback=feedback)['OUTPUT']

                    log_info(feedback, log_path, "✅ GWR-Gebäudescores berechnet")
                else:  # Kantonale Daten
                    log_info(feedback, log_path, "📌 Verwende kantonale Gebäudedaten")
                    output_name = "gebaeudeinformationen_kt_bs_lv95.gpkg"
                    gefiltert = processing.run("native:extractbyexpression", {
                        'INPUT': layer,
                        'EXPRESSION': '"GEBSTATUS" = 1004',
                        'OUTPUT': 'memory:'
                    }, context=context, feedback=feedback)['OUTPUT']

                    # Gebäudekategorien hinzufügen
                    kategotxt = processing.run("native:fieldcalculator", {
                        'INPUT': gefiltert,
                        'FIELD_NAME': 'KATEGOTXT',
                        'FIELD_TYPE': 2,  # String
                        'FIELD_LENGTH': 80,
                        'NEW_FIELD': True,
                        'FORMULA': _get_kt_gebaeude_formula(),
                        'OUTPUT': 'memory:'
                    }, context=context, feedback=feedback)['OUTPUT']

                    # Gebäudescores berechnen
                    export_layer = processing.run("native:fieldcalculator", {
                        'INPUT': kategotxt,
                        'FIELD_NAME': 'score_gebaeude',
                        'FIELD_TYPE': 1,  # Integer
                        'FIELD_LENGTH': 10,
                        'NEW_FIELD': True,
                        'FORMULA': _get_kt_score_formula(),
                        'OUTPUT': 'memory:'
                    }, context=context, feedback=feedback)['OUTPUT']

                    log_info(feedback, log_path, "✅ Kantonale Gebäudescores berechnet")
            else:
                # Für alle anderen Layer
                export_layer = layer
                if layername:
                    log_info(feedback, log_path, f"📋 Verwende Layer: {layername}")

            # Export durchführen, wenn output_name definiert ist
            if output_name and export_layer:
                export_path = os.path.join(prepared_input_path, output_name)
                processing.run("native:savefeatures", {
                    "INPUT": export_layer,
                    "OUTPUT": export_path
                }, context=context, feedback=feedback)
                log_info(feedback, log_path, f"💾 Layer exportiert: {output_name}")
            else:
                log_info(feedback, log_path, f"⚠️ Kein Export möglich für: {key}")

        except Exception as e:
            log_info(feedback, log_path, f"❌ Fehler bei {key}: {str(e)}")
            raise QgsProcessingException(f"Fehler bei der Verarbeitung von {key}: {str(e)}")

    log_info(feedback, log_path, "✅ Phase 1 abgeschlossen: Alle Layer vorbereitet")

def _get_gwr_gebaeude_formula() -> str:
    """Gibt die Formel für GWR-Gebäudebezeichnungen zurück."""
    return '''
        CASE 
        WHEN "GKLAS" = 1110 THEN 'Gebaeude mit einer Wohnung'
        WHEN "GKLAS" = 1121 THEN 'Gebaeude mit zwei Wohnungen'
        WHEN "GKLAS" = 1122 THEN 'Gebaeude mit drei oder mehr Wohnungen'
        WHEN "GKLAS" = 1130 THEN 'Wohngebaeude fuer Gemeinschaften'
        WHEN "GKLAS" = 1211 THEN 'Hotelgebaeude'
        WHEN "GKLAS" = 1212 THEN 'Andere Gebaeude fuer kurzfristige Beherbergung'
        WHEN "GKLAS" = 1220 THEN 'Buerogebaeude'
        WHEN "GKLAS" = 1230 THEN 'Gross- und Einzelhandelsgebaeude'
        WHEN "GKLAS" = 1231 THEN 'Restaurants und Bars in Gebaeuden ohne Wohnnutzung'
        WHEN "GKLAS" = 1241 THEN 'Gebaeude des Verkehrs- und Nachrichtenwesens ohne Garagen'
        WHEN "GKLAS" = 1242 THEN 'Garagengebaeude'
        WHEN "GKLAS" = 1251 THEN 'Industriegebaeude'
        WHEN "GKLAS" = 1252 THEN 'Behaelter, Silos und Lagergebaeude'
        WHEN "GKLAS" = 1261 THEN 'Gebaeude fuer Kultur- und Freizeitzwecke'
        WHEN "GKLAS" = 1262 THEN 'Museen und Bibliotheken'
        WHEN "GKLAS" = 1263 THEN 'Schul- und Hochschulgebaeude, Forschungseinrichtungen'
        WHEN "GKLAS" = 1264 THEN 'Krankenhaeuser und Facheinrichtungen des Gesundheitswesens'
        WHEN "GKLAS" = 1265 THEN 'Sporthallen'
        WHEN "GKLAS" = 1271 THEN 'Landwirtschaftliche Betriebsgebaeude'
        WHEN "GKLAS" = 1272 THEN 'Kirchen und sonstige Kultgebaeude'
        WHEN "GKLAS" = 1273 THEN 'Denkmaeler oder unter Denkmalschutz stehende Bauwerke'
        WHEN "GKLAS" = 1274 THEN 'Sonstige Hochbauten, anderweitig nicht genannt'
        WHEN "GKLAS" = 1275 THEN 'Andere Gebaeude fuer die kollektive Unterkunft'
        WHEN "GKLAS" = 1276 THEN 'Gebaeude fuer die Tierhaltung'
        WHEN "GKLAS" = 1277 THEN 'Gebaeude fuer den Pflanzenbau'
        WHEN "GKLAS" = 1278 THEN 'Andere landwirtschaftliche Gebaeude'
        ELSE NULL END
    '''

def _get_gwr_score_formula() -> str:
    """Gibt die Formel für GWR-Gebäudescores zurück."""
    return '''
        CASE 
        WHEN "GKLAS" = 1110 THEN 3
        WHEN "GKLAS" = 1121 THEN 3
        WHEN "GKLAS" = 1122 THEN 3
        WHEN "GKLAS" = 1130 THEN 2
        WHEN "GKLAS" = 1211 THEN 1
        WHEN "GKLAS" = 1212 THEN 1
        WHEN "GKLAS" = 1220 THEN 0
        WHEN "GKLAS" = 1230 THEN 1
        WHEN "GKLAS" = 1231 THEN 1
        WHEN "GKLAS" = 1241 THEN -2
        WHEN "GKLAS" = 1242 THEN -2
        WHEN "GKLAS" = 1251 THEN -3
        WHEN "GKLAS" = 1252 THEN -3
        WHEN "GKLAS" = 1261 THEN 2
        WHEN "GKLAS" = 1262 THEN 1
        WHEN "GKLAS" = 1263 THEN 1
        WHEN "GKLAS" = 1264 THEN -2
        WHEN "GKLAS" = 1265 THEN 0
        WHEN "GKLAS" = 1271 THEN -2
        WHEN "GKLAS" = 1272 THEN 0
        WHEN "GKLAS" = 1273 THEN -2
        WHEN "GKLAS" = 1274 THEN -1
        WHEN "GKLAS" = 1275 THEN 1
        WHEN "GKLAS" = 1276 THEN -3
        WHEN "GKLAS" = 1277 THEN -3
        WHEN "GKLAS" = 1278 THEN -3
        ELSE NULL END
    '''

def _get_kt_gebaeude_formula() -> str:
    """Gibt die Formel für kantonale Gebäudekategorien zurück."""
    return '''
        CASE 
        WHEN "GEBKATEGO" = 1021 THEN 'Wohngebaeude reines Wohngebaeude'
        WHEN "GEBKATEGO" = 1025 THEN 'Wohngebaeude reines Wohngebaeude (Mehrfamilienhaus)'
        WHEN "GEBKATEGO" = 1030 THEN 'Wohngebaeude uebrige Wohngebaeude'
        WHEN "GEBKATEGO" = 1040 THEN 'Anderes Gebäude mit Wohnnutzung (nebensaechlich)'
        WHEN "GEBKATEGO" = 1060 THEN 'Anderes Gebäude ohne Wohnnutzung'
        WHEN "GEBKATEGO" = 1080 THEN 'Sonderbaute'
        WHEN "GEBKATEGO" = 1090 THEN 'Gebaeudekategorie unbekannt'
        ELSE NULL END
    '''

def _get_kt_score_formula() -> str:
    """Gibt die Formel für kantonale Gebäudescores zurück."""
    return '''
        CASE 
        WHEN "GEBKATEGO" = 1021 THEN 2
        WHEN "GEBKATEGO" = 1025 THEN 3
        WHEN "GEBKATEGO" = 1030 THEN 1
        WHEN "GEBKATEGO" = 1040 THEN 0
        WHEN "GEBKATEGO" = 1060 THEN -1
        WHEN "GEBKATEGO" = 1080 THEN -3
        WHEN "GEBKATEGO" = 1090 THEN -2
        ELSE NULL END
    '''

def calculate_quantile_score(layer: QgsVectorLayer, field_name: str, output_field: str, feedback: QgsProcessingFeedback) -> None:
    """
    Berechnet eine Quantilskala für ein numerisches Feld.
    
    Diese Version verwendet QGIS-Verarbeitungsalgorithmen für die Berechnung der Quantile,
    was kompatibel mit allen Datenprovidern ist.
    
    Args:
        layer: Der zu bewertende Layer
        field_name: Name des zu bewertenden Feldes
        output_field: Name des Ausgabefeldes für die Scores
        feedback: Feedback-Objekt für Fortschrittsmeldungen
        
    Raises:
        QgsProcessingException: Bei Fehlern während der Berechnung
    """
    try:
        # Feldnamen validieren
        field_names = [f.name() for f in layer.fields()]
        if field_name not in field_names:
            raise QgsProcessingException(f"Feld '{field_name}' existiert nicht. Verfügbare Felder: {', '.join(field_names)}")
            
        feedback.pushInfo(f"Verfügbare Felder: {', '.join(field_names)}")
        feedback.pushInfo(f"Verwende Feld: {field_name}")
        
        # Werte sammeln
        values = []
        for feature in layer.getFeatures():
            value = feature[field_name]
            if value is not None and value != NULL and value != '':
                try:
                    values.append(float(value))
                except (ValueError, TypeError):
                    feedback.pushWarning(f"Ungültiger Wert übersprungen: {value}")
                    
        if not values:
            raise QgsProcessingException(f"Keine gültigen Werte im Feld '{field_name}' gefunden")
            
        # Werte sortieren und Quantile berechnen
        values.sort()
        n = len(values)
        feedback.pushInfo(f"Anzahl gültiger Werte: {n}")
        
        # Quantile berechnen
        quantiles = {
            'q1': values[int(n * 0.142857)],
            'q2': values[int(n * 0.285714)],
            'q3': values[int(n * 0.428571)],
            'q4': values[int(n * 0.571429)],
            'q5': values[int(n * 0.714286)],
            'q6': values[int(n * 0.857143)]
        }
        
        # Neues Feld hinzufügen
        provider = layer.dataProvider()
        if output_field not in field_names:
            provider.addAttributes([QgsField(output_field, QVariant.Int)])
            layer.updateFields()
            
        # Scores berechnen und speichern
        layer.startEditing()
        updated_count = 0
        for feature in layer.getFeatures():
            value = feature[field_name]
            if value is not None and value != NULL and value != '':
                try:
                    value = float(value)
                    score = -3  # Default-Wert
                    if value <= quantiles['q1']:
                        score = -3
                    elif value <= quantiles['q2']:
                        score = -2
                    elif value <= quantiles['q3']:
                        score = -1
                    elif value <= quantiles['q4']:
                        score = 0
                    elif value <= quantiles['q5']:
                        score = 1
                    elif value <= quantiles['q6']:
                        score = 2
                    else:
                        score = 3
                        
                    feature[output_field] = score
                    layer.updateFeature(feature)
                    updated_count += 1
                except (ValueError, TypeError):
                    feedback.pushWarning(f"Ungültiger Wert übersprungen: {value}")
                    
        layer.commitChanges()
        layer.updateExtents()
        
        # Statistiken für Feedback
        feedback.pushInfo(f"📊 Quantile für '{field_name}':")
        for q, v in quantiles.items():
            feedback.pushInfo(f"  {q}: {v:.2f}")
        feedback.pushInfo(f"✅ {updated_count} Features mit Scores aktualisiert")
        feedback.pushInfo(f"✅ Neues Feld '{output_field}' mit quantilen Scores gespeichert.")
        
    except Exception as e:
        if layer.isEditable():
            layer.rollBack()
        raise QgsProcessingException(f"Fehler bei der Quantilberechnung: {str(e)}")

def phase_2_bereinigung_segmentierung(
    prepared_input_path: str,
    temp_path: str,
    final_output_dir: str,
    log_info: callable,
    log_path: str,
    context: QgsProcessingContext,
    feedback: QgsProcessingFeedback
) -> None:
    """
    Führt Phase 2 (Bereinigung & Segmentierung Mobilitätsnetz) im Superblock-Prozess durch.
    
    Diese Phase:
    1. Filtert das Mobilitätsnetz nach Hierarchie
    2. Erstellt Ausschlussflächen aus verschiedenen Netzen
    3. Bereinigt das Mobilitätsnetz durch räumlichen Filter
    4. Segmentiert das Netz in Komponenten
    5. Identifiziert Stützpunkte
    6. Erstellt einen finalen Netzbuffer
    
    Args:
        prepared_input_path: Verzeichnis mit vorbereiteten Eingabedaten
        temp_path: Verzeichnis für temporäre Daten
        final_output_dir: Verzeichnis für finale Ausgaben
        log_info: Funktion für kombiniertes Logging
        log_path: Pfad zur Logdatei
        context: QGIS Processing Kontext
        feedback: Feedback-Objekt für Fortschrittsmeldungen
        
    Raises:
        QgsProcessingException: Bei Fehlern während der Verarbeitung
    """
    if not all(os.path.exists(p) for p in [prepared_input_path, temp_path, final_output_dir]):
        raise QgsProcessingException("Ein oder mehrere Verzeichnisse existieren nicht")

    log_info(feedback, log_path, "🚦 Starte Phase 2: Bereinigung & Segmentierung des Mobilitätsnetzes")
    log_info(feedback, log_path, f"📂 Verzeichnisse: {prepared_input_path}, {temp_path}, {final_output_dir}")

    try:
        # Pfade definieren
        mobilitaetsnetz_input = os.path.join(prepared_input_path, "mobilitaetsnetz_lv95.gpkg")
        if not os.path.exists(mobilitaetsnetz_input):
            raise QgsProcessingException(f"Mobilitätsnetz nicht gefunden: {mobilitaetsnetz_input}")

        # Zielpfade im _tempdata
        gefiltert_path = os.path.join(temp_path, "mobilitaetsnetz_gefiltert.gpkg")
        bereinigt_path = os.path.join(temp_path, "mobilitaetsnetz_bereinigt.gpkg")
        dissolved_path = os.path.join(temp_path, "mobilitaetsnetz_dissolved.gpkg")
        exploded_path = os.path.join(temp_path, "mobilitaetsnetz_exploded.gpkg")
        stuetzpunkte_path = os.path.join(temp_path, "stuetzpunkte_dissolved.gpkg")
        stuetzpunkte_mit_anzahl_path = os.path.join(temp_path, "stuetzpunkte_mit_anzahl.gpkg")
        stuetzpunkte_ab3_path = os.path.join(temp_path, "stuetzpunkte_ab3.gpkg")
        komponenten_path = os.path.join(temp_path, "mobilitaetsnetz_komponenten.gpkg")
        verknuepfte_punkte_path = os.path.join(temp_path, "stuetzpunkte_ab3_mit_gruppe.gpkg")
        buffer_finalnetz_path = os.path.join(temp_path, "mobilitaetsnetz_bereinigt_buffer.gpkg")
        merged_buffer_path = os.path.join(temp_path, "buffer_merged.gpkg")
        dissolved_buffer_path = os.path.join(temp_path, "buffer_dissolved.gpkg")

        # Schritt 1: Filtern mit Validierung
        log_info(feedback, log_path, "🔎 Filtere Mobilitätsnetz nach Hierarchie...")
        result = processing.run("native:extractbyexpression", {
            'INPUT': mobilitaetsnetz_input,
            'EXPRESSION': '"Strassennetzhierarchie" IN (\'QSS\', \'ES\')',
            'OUTPUT': gefiltert_path
        }, context=context, feedback=feedback)
        
        if not result or not os.path.exists(gefiltert_path):
            raise QgsProcessingException("Fehler beim Filtern des Mobilitätsnetzes")
            
        filtered_layer = QgsVectorLayer(gefiltert_path, "gefiltert", "ogr")
        if not filtered_layer.isValid() or filtered_layer.featureCount() == 0:
            raise QgsProcessingException("Gefiltertes Netz ist ungültig oder leer")
            
        log_info(feedback, log_path, f"✅ Gefiltertes Netz gespeichert: {gefiltert_path}")

        # Schritt 2–4: Ausschlusslayer puffern mit Validierung
        log_info(feedback, log_path, f"➕ Erstelle Ausschluss-Puffer ({BUFFER_DISTANCES['ausschluss']} m)...")
        buffer_defs = [
            ("trp_velo_lv95.gpkg", "buffer_velo.gpkg"),
            ("liniennetz_oev_lv95.gpkg", "buffer_oev.gpkg"),
            ("ausnahmetransportrouten_lv95.gpkg", "buffer_ausnahme.gpkg"),
            ("lifeline_notfallachsen_lv95.gpkg", "buffer_notfall.gpkg")
        ]
        
        buffer_files = []
        for fname, buffer_out in buffer_defs:
            input_path = os.path.join(prepared_input_path, fname)
            if not os.path.exists(input_path):
                log_info(feedback, log_path, f"⚠️ Ausschlusslayer nicht gefunden: {fname}")
                continue
                
            output_path = os.path.join(temp_path, buffer_out)
            result = processing.run("native:buffer", {
                'INPUT': input_path,
                'DISTANCE': BUFFER_DISTANCES['ausschluss'],
                'SEGMENTS': 5,
                'END_CAP_STYLE': 0,
                'JOIN_STYLE': 0,
                'MITER_LIMIT': 2,
                'DISSOLVE': False,
                'OUTPUT': output_path
            }, context=context, feedback=feedback)
            
            if not result or not os.path.exists(output_path):
                log_info(feedback, log_path, f"⚠️ Fehler beim Erstellen des Puffers: {buffer_out}")
                continue
                
            buffer_layer = QgsVectorLayer(output_path, "buffer", "ogr")
            if not buffer_layer.isValid() or buffer_layer.featureCount() == 0:
                log_info(feedback, log_path, f"⚠️ Ungültiger oder leerer Puffer: {buffer_out}")
                continue
                
            buffer_files.append(output_path)
            log_info(feedback, log_path, f"✅ Puffer erstellt: {output_path}")

        if not buffer_files:
            raise QgsProcessingException("Keine gültigen Ausschlusslayer gefunden")

        # Merge und Dissolve der Puffer
        log_info(feedback, log_path, "🔄 Führe Puffer zusammen...")
        result = processing.run("native:mergevectorlayers", {
            'LAYERS': buffer_files,
            'CRS': None,
            'OUTPUT': merged_buffer_path
        }, context=context, feedback=feedback)
        
        if not result or not os.path.exists(merged_buffer_path):
            raise QgsProcessingException("Fehler beim Zusammenführen der Puffer")
            
        log_info(feedback, log_path, f"✅ Puffer zusammengeführt: {merged_buffer_path}")

        log_info(feedback, log_path, "🔄 Löse Puffer auf...")
        result = processing.run("native:dissolve", {
            'INPUT': merged_buffer_path,
            'FIELD': [],
            'OUTPUT': dissolved_buffer_path
        }, context=context, feedback=feedback)
        
        if not result or not os.path.exists(dissolved_buffer_path):
            raise QgsProcessingException("Fehler beim Auflösen der Puffer")
            
        dissolved_layer = QgsVectorLayer(dissolved_buffer_path, "dissolved", "ogr")
        if not dissolved_layer.isValid() or dissolved_layer.featureCount() == 0:
            raise QgsProcessingException("Aufgelöster Puffer ist ungültig oder leer")
            
        log_info(feedback, log_path, f"✅ Puffer aufgelöst: {dissolved_buffer_path}")

        # Schritt 5: Bereinigung des Netzes durch räumlichen Filter
        log_info(feedback, log_path, "🧹 Entferne Mobilitätsnetz-Elemente innerhalb Ausschlussfläche...")
        
        # Extrahiere Linien ausserhalb der Puffer
        result = processing.run("native:extractbylocation", {
            'INPUT': gefiltert_path,
            'PREDICATE': [0],  # intersects
            'INTERSECT': dissolved_buffer_path,
            'OUTPUT': 'memory:'
        }, context=context, feedback=feedback)
        
        if not result or 'OUTPUT' not in result:
            raise QgsProcessingException("Fehler beim Finden der schneidenden Linien")
            
        intersecting_lines = result['OUTPUT']
        
        # Extrahiere Linien, die nicht mit dem Puffer schneiden
        result = processing.run("native:extractbylocation", {
            'INPUT': gefiltert_path,
            'PREDICATE': [0],  # intersects
            'INTERSECT': intersecting_lines,
            'OUTPUT': bereinigt_path,
            'NEGATE': True  # Negiere die Auswahl
        }, context=context, feedback=feedback)
        
        if not result or not os.path.exists(bereinigt_path):
            raise QgsProcessingException("Fehler beim Bereinigen des Netzes")
            
        bereinigt_layer = QgsVectorLayer(bereinigt_path, "bereinigt", "ogr")
        if not bereinigt_layer.isValid() or bereinigt_layer.featureCount() == 0:
            raise QgsProcessingException("Bereinigtes Netz ist ungültig oder leer")
            
        log_info(feedback, log_path, f"✅ Bereinigtes Netz: {bereinigt_path}")

        # Schritt 6: dissolve
        processing.run("native:dissolve", {
            'INPUT': bereinigt_path,
            'FIELD': [],
            'OUTPUT': dissolved_path
        }, context=context, feedback=feedback)

        # Schritt 7: explode
        processing.run("qgis:explodelines", {
            'INPUT': dissolved_path,
            'OUTPUT': exploded_path
        }, context=context, feedback=feedback)

        # Schritt 8: Stützpunkte extrahieren
        processing.run("native:extractvertices", {
            'INPUT': dissolved_path,
            'OUTPUT': stuetzpunkte_path
        }, context=context, feedback=feedback)

        # Schritt 9: Spatial Indices
        processing.run("native:createspatialindex", {'INPUT': stuetzpunkte_path}, context=context, feedback=feedback)
        processing.run("native:createspatialindex", {'INPUT': exploded_path}, context=context, feedback=feedback)

        # Schritt 10: Linien zählen an Stützpunkten
        processing.run("native:joinbylocationsummary", {
            'INPUT': stuetzpunkte_path,
            'JOIN': exploded_path,
            'PREDICATE': [0, 3],  # intersects, touches
            'JOIN_FIELDS': ['OBJECTID'],
            'SUMMARIES': [0],  # count
            'DISCARD_NONMATCHING': False,
            'OUTPUT': stuetzpunkte_mit_anzahl_path
        }, context=context, feedback=feedback)

        # Schritt 11: Filtere Punkte mit ≥ 3 Verbindungen
        processing.run("native:extractbyattribute", {
            'INPUT': stuetzpunkte_mit_anzahl_path,
            'FIELD': 'OBJECTID_count',
            'OPERATOR': 2,  # >
            'VALUE': '2',
            'OUTPUT': stuetzpunkte_ab3_path
        }, context=context, feedback=feedback)

        # Schritt 12: Komponenten analysieren (GRASS)
        processing.run("grass7:v.net.components", {
            'input': exploded_path,
            'points': None,
            'threshold': 50,
            'method': 0,
            '-a': True,
            'output': komponenten_path,
            'output_point': 'TEMPORARY_OUTPUT',
            'GRASS_REGION_PARAMETER': None,
            'GRASS_SNAP_TOLERANCE_PARAMETER': -1,
            'GRASS_MIN_AREA_PARAMETER': 0.0001,
            'GRASS_OUTPUT_TYPE_PARAMETER': 0
        }, context=context, feedback=feedback)

        # Schritt 13: Komponentennummer an Stützpunkte übertragen
        processing.run("qgis:joinbynearest", {
            'INPUT': stuetzpunkte_ab3_path,
            'INPUT_2': komponenten_path,
            'FIELDS_TO_COPY': ['comp'],
            'DISCARD_NONMATCHING': False,
            'MAX_DISTANCE': 1.0,
            'NEIGHBORS': 1,
            'OUTPUT': verknuepfte_punkte_path
        }, context=context, feedback=feedback)

        # Schritt 14: Buffer um bereinigtes Netz
        processing.run("native:buffer", {
            'INPUT': bereinigt_path,
            'DISTANCE': BUFFER_DISTANCES['netz'],
            'SEGMENTS': 5,
            'END_CAP_STYLE': 0,
            'JOIN_STYLE': 0,
            'MITER_LIMIT': 2,
            'OUTPUT': buffer_finalnetz_path
        }, context=context, feedback=feedback)
        log_info(feedback, log_path, f"✅ Finaler Netzbuffer ({BUFFER_DISTANCES['netz']} m) gespeichert: {buffer_finalnetz_path}")

        # Finaler Export
        bereinigt_final_path = os.path.join(final_output_dir, "mobilitaetsnetz_bereinigt.gpkg")
        shutil.copyfile(bereinigt_path, bereinigt_final_path)
        log_info(feedback, log_path, f"📦 Bereinigtes Netz in Final-Ordner kopiert: {bereinigt_final_path}")

        log_info(feedback, log_path, "🏁 Phase 2 (Bereinigung & Segmentierung) abgeschlossen.")

    except Exception as e:
        log_info(feedback, log_path, f"❌ Fehler in Phase 2: {str(e)}")
        raise QgsProcessingException(f"Fehler in Phase 2: {str(e)}")

def phase_3_gebietsanalyse(
    final_path: str,
    temp_path: str,
    result_paths: Dict[str, str],
    context: QgsProcessingContext,
    feedback: QgsProcessingFeedback,
    log_info: callable,
    log_path: str
) -> None:
    """
    Führt Phase 3 (Geobasierte Analyse) im Superblock-Prozess durch.
    
    Diese Phase:
    1. Erstellt einen negativen Buffer um die Ausschlussfläche
    2. Erstellt einen positiven Buffer um das bereinigte Netz
    3. Extrahiert Liegenschaften im negativen Ausschlussbuffer
    4. Filtert Liegenschaften mit mindestens einem Gebäude
    5. Verschmilzt und teilt die Flächen
    6. Extrahiert geeignete Blocks im positiven Netzbuffer
    
    Args:
        final_path: Pfad zum bereinigten Mobilitätsnetz
        temp_path: Verzeichnis für temporäre Daten
        result_paths: Dictionary mit Pfaden zu wichtigen Layern
        context: QGIS Processing Kontext
        feedback: Feedback-Objekt für Fortschrittsmeldungen
        log_info: Funktion für kombiniertes Logging
        log_path: Pfad zur Logdatei
        
    Raises:
        QgsProcessingException: Bei Fehlern während der Verarbeitung oder wenn erforderliche Layer fehlen
    """
    # Validierung der Eingabepfade
    required_keys = ["buffer", "liegenschaften", "gebaeude"]
    missing_keys = [key for key in required_keys if key not in result_paths or not os.path.exists(result_paths[key])]
    if missing_keys:
        raise QgsProcessingException(f"Erforderliche Layer fehlen: {', '.join(missing_keys)}")

    if not os.path.exists(final_path):
        raise QgsProcessingException(f"Bereinigtes Netz nicht gefunden: {final_path}")

    log_info(feedback, log_path, "📍 Starte Phase 3: Geobasierte Gebietsanalyse")
    log_info(feedback, log_path, f"📂 Verzeichnisse: {temp_path}")

    try:
        # 1. Negativer Buffer um Ausschlussfläche
        buffer_ausschluss = os.path.join(temp_path, "1_buffer_dissolved_ausschluss.gpkg")
        processing.run("native:buffer", {
            'INPUT': result_paths["buffer"],
            'DISTANCE': BUFFER_DISTANCES['negativ'],
            'SEGMENTS': 5,
            'END_CAP_STYLE': 0,
            'JOIN_STYLE': 0,
            'MITER_LIMIT': 2,
            'DISSOLVE': False,
            'SEPARATE_DISJOINT': False,
            'OUTPUT': buffer_ausschluss
        }, context=context, feedback=feedback)
        log_info(feedback, log_path, f"✅ Negativer Buffer erstellt: {buffer_ausschluss}")

        # 2. Positiver Buffer um bereinigtes Netz
        final_buffer = os.path.join(temp_path, "mobilitaetsnetz_finalbuffer.gpkg")
        processing.run("native:buffer", {
            'INPUT': final_path,
            'DISTANCE': BUFFER_DISTANCES['final'],
            'SEGMENTS': 5,
            'END_CAP_STYLE': 1,
            'JOIN_STYLE': 1,
            'MITER_LIMIT': 2,
            'DISSOLVE': False,
            'SEPARATE_DISJOINT': False,
            'OUTPUT': final_buffer
        }, context=context, feedback=feedback)
        log_info(feedback, log_path, f"✅ Positiver Buffer erstellt: {final_buffer}")
        result_paths["final_buffer"] = final_buffer

        # 3. Extrahiere Liegenschaften im negativen Ausschlussbuffer
        liegenschaften_path = result_paths["liegenschaften"]
        ausschluss_layer = os.path.join(temp_path, "2_liegenschaftsflaechen_ausschlusslayer.gpkg")
        processing.run("native:extractbylocation", {
            'INPUT': liegenschaften_path,
            'PREDICATE': [2],  # contains
            'INTERSECT': buffer_ausschluss,
            'OUTPUT': ausschluss_layer
        }, context=context, feedback=feedback)
        log_info(feedback, log_path, f"✅ Ausschlussflächen extrahiert: {ausschluss_layer}")

        # 4. Extrahiere Liegenschaften mit mind. 1 Gebäude
        mit_gebaeude_path = os.path.join(temp_path, "2_liegenschaftsflaechen_gebaeude.gpkg")
        processing.run("native:extractbylocation", {
            'INPUT': ausschluss_layer,
            'PREDICATE': [1, 4],  # touches, overlaps
            'INTERSECT': result_paths["gebaeude"],
            'OUTPUT': mit_gebaeude_path
        }, context=context, feedback=feedback)
        log_info(feedback, log_path, f"✅ Ausschlussflächen mit Gebäuden extrahiert: {mit_gebaeude_path}")

        # 5. Verschmelze diese Flächen
        verschmolzen_path = os.path.join(temp_path, "3_liegenschaftsflaechen_verschmelzen.gpkg")
        processing.run("native:dissolve", {
            'INPUT': mit_gebaeude_path,
            'FIELD': [],
            'SEPARATE_DISJOINT': False,
            'OUTPUT': verschmolzen_path
        }, context=context, feedback=feedback)
        log_info(feedback, log_path, f"✅ Flächen verschmolzen: {verschmolzen_path}")

        # 6. Multipart zu Singleparts
        split_path = os.path.join(temp_path, "3_liegenschaftsflaechen_split.gpkg")
        processing.run("native:multiparttosingleparts", {
            'INPUT': verschmolzen_path,
            'OUTPUT': split_path
        }, context=context, feedback=feedback)
        log_info(feedback, log_path, f"✅ Multipart-Polygone aufgelöst: {split_path}")

        # 7. Blocks extrahieren, die im positiven Netzbuffer liegen
        blocks_path = os.path.join(temp_path, "4_liegenschaftsflaechen_blocks.gpkg")
        processing.run("native:extractbylocation", {
            'INPUT': split_path,
            'PREDICATE': [0],  # intersects
            'INTERSECT': final_buffer,
            'OUTPUT': blocks_path
        }, context=context, feedback=feedback)
        log_info(feedback, log_path, f"✅ Geeignete Blocks extrahiert: {blocks_path}")

        # Ergebnis zurückgeben
        result_paths["singleparts"] = blocks_path
        log_info(feedback, log_path, "🏁 Phase 3 (Gebietsanalyse) abgeschlossen.")

    except Exception as e:
        log_info(feedback, log_path, f"❌ Fehler in Phase 3: {str(e)}")
        raise QgsProcessingException(f"Fehler in Phase 3: {str(e)}")

def phase_4_quantilskala(
    temp_path: str,
    final_output_dir: str,
    context: QgsProcessingContext,
    feedback: QgsProcessingFeedback,
    log_info: callable,
    log_path: str,
    geb_weight: int = 80,
    verh_weight: int = 20
) -> None:
    """
    Führt Phase 4 (Quantilbasierte Bewertung) im Superblock-Prozess durch.
    """
    from qgis.core import QgsVectorLayer, QgsField

    try:
        if not all(os.path.exists(p) for p in [temp_path, final_output_dir]):
            raise QgsProcessingException("Ein oder mehrere Verzeichnisse existieren nicht")

        if geb_weight + verh_weight != 100:
            raise QgsProcessingException("Die Summe der Gewichtungen muss 100% ergeben")

        log_info(feedback, log_path, f"📊 Starte Phase 4: Quantil-Skalierung und Verhältnisbewertung")
        log_info(feedback, log_path, f"⚖️ Gewichtungen: Gebäude {geb_weight}%, Verhältnis {verh_weight}%")
        log_info(feedback, log_path, f"📂 Verzeichnisse: {temp_path}, {final_output_dir}")

        # Eingabelayer vorbereiten
        input_blocks = os.path.join(temp_path, "4_liegenschaftsflaechen_blocks.gpkg")
        if not os.path.exists(input_blocks):
            raise QgsProcessingException(f"Blocks-Layer nicht gefunden: {input_blocks}")
        log_info(feedback, log_path, f"✅ Blocks-Layer gefunden: {input_blocks}")

        join_layer = os.path.join(temp_path.replace("_tempdata", "_prepared_inputdata"), "gwr_bund_bs_lv95.gpkg")
        if not os.path.exists(join_layer):
            raise QgsProcessingException(f"Gebäude-Layer nicht gefunden: {join_layer}")
        log_info(feedback, log_path, f"✅ Gebäude-Layer gefunden: {join_layer}")

        # Gebäudescores aggregieren
        log_info(feedback, log_path, "🔄 Aggregiere Gebäudescores...")
        joined_score_path = os.path.join(temp_path, "1_liegenschaftsflaechen_score_geb_sum.gpkg")

        # Aggregation durchführen
        result = processing.run("native:joinbylocationsummary", {
            'INPUT': input_blocks,
            'PREDICATE': [1],  # contains
            'JOIN': f'{join_layer}|layername=gwr_bund_bs_lv95',
            'JOIN_FIELDS': ['score_gebaeude'],
            'SUMMARIES': [5],  # sum
            'DISCARD_NONMATCHING': False,
            'OUTPUT': joined_score_path
        }, context=context, feedback=feedback)
        
        if not result or not os.path.exists(joined_score_path):
            raise QgsProcessingException("Fehler beim Aggregieren der Gebäudescores")
        
        # Layer nach erfolgreicher Aggregation laden
        layer = QgsVectorLayer(joined_score_path, "joined_layer", "ogr")
        if not layer.isValid():
            raise QgsProcessingException("Layer nach Join ist ungültig")
            
        log_info(feedback, log_path, f"✅ Gebäudescores aggregiert: {layer.featureCount()} Features")
        
        # Feldnamen überprüfen
        field_names = [f.name() for f in layer.fields()]
        score_field = 'score_gebaeude_sum'  # Korrekter Feldname nach dem Join
        if score_field not in field_names:
            raise QgsProcessingException(f"Feld '{score_field}' nicht gefunden. Verfügbare Felder: {', '.join(field_names)}")
        log_info(feedback, log_path, f"✅ Verfügbare Felder: {', '.join(field_names)}")

        # Quantil-Skalierung anwenden
        log_info(feedback, log_path, "🔄 Führe Quantil-Skalierung durch...")
        calculate_quantile_score(layer, score_field, "score_geb_sum_norm", feedback)

        # Bounding Box berechnen
        bbox_path = os.path.join(temp_path, "2_liegenschaftsflaechen_min_boundingbox.gpkg")
        log_info(feedback, log_path, "🔄 Berechne Bounding Boxes...")
        processing.run("native:orientedminimumboundingbox", {
            'INPUT': joined_score_path,
            'OUTPUT': bbox_path
        }, context=context, feedback=feedback)
        log_info(feedback, log_path, f"✅ Bounding Boxes erstellt: {bbox_path}")

        # Verhältnisbewertung
        scored_bbox_path = os.path.join(temp_path, "3_liegenschaftsflaechen_min_bbox_score_verhaeltnis.gpkg")
        log_info(feedback, log_path, "🔄 Berechne Verhältnisbewertung...")
        processing.run("native:fieldcalculator", {
            'INPUT': bbox_path,
            'FIELD_NAME': 'score_verhaeltnis',
            'FIELD_TYPE': 1,  # Integer
            'FIELD_LENGTH': 10,
            'FIELD_PRECISION': 0,
            'FORMULA': (
                "with_variable('p', 100.0 * \"width\" / \"height\", "
                "CASE "
                "WHEN @p >= 85.7 THEN 3 "
                "WHEN @p >= 71.4 THEN 2 "
                "WHEN @p >= 57.1 THEN 1 "
                "WHEN @p >= 42.8 THEN 0 "
                "WHEN @p >= 28.5 THEN -1 "
                "WHEN @p >= 14.2 THEN -2 "
                "ELSE -3 END)"
            ),
            'OUTPUT': scored_bbox_path
        }, context=context, feedback=feedback)
        log_info(feedback, log_path, f"✅ Verhältnisbewertung berechnet: {scored_bbox_path}")

        # Temporärer Layer für den Join
        temp_joined_path = os.path.join(temp_path, "temp_joined_scores.gpkg")
        log_info(feedback, log_path, "🔄 Führe Scores zusammen...")
        processing.run("native:joinattributestable", {
            'INPUT': joined_score_path,
            'FIELD': 'fid',
            'INPUT_2': scored_bbox_path,
            'FIELD_2': 'fid',
            'FIELDS_TO_COPY': ['score_verhaeltnis'],
            'METHOD': 0,  # Erstelle neue Felder
            'DISCARD_NONMATCHING': False,
            'PREFIX': '',
            'OUTPUT': temp_joined_path
        }, context=context, feedback=feedback)
        log_info(feedback, log_path, "✅ Scores zusammengeführt")

        # Temporärer Layer für den finalen Score
        temp_final_path = os.path.join(temp_path, "temp_final_scores.gpkg")
        log_info(feedback, log_path, "🔄 Berechne gewichteten finalen Score...")
        formula = f'round({geb_weight/100} * "score_geb_sum_norm" + {verh_weight/100} * "score_verhaeltnis")'
        log_info(feedback, log_path, f"Verwendete Formel: {formula}")
        
        processing.run("native:fieldcalculator", {
            'INPUT': temp_joined_path,
            'FIELD_NAME': 'final_score',
            'FIELD_TYPE': 1,  # Integer
            'FIELD_LENGTH': 10,
            'FIELD_PRECISION': 0,
            'FORMULA': formula,
            'OUTPUT': temp_final_path
        }, context=context, feedback=feedback)
        log_info(feedback, log_path, f"✅ Gewichteter finaler Score berechnet (Gebäude: {geb_weight}%, Verhältnis: {verh_weight}%)")

        # Finale Datei mit nur den gewünschten Attributen
        final_path = os.path.join(final_output_dir, "3_liegenschaftsflaechen_joined_scores.gpkg")
        log_info(feedback, log_path, "🔄 Reduziere auf gewünschte Attribute...")
        processing.run("native:retainfields", {
            'INPUT': temp_final_path,
            'FIELDS': ['fid', 'score_gebaeude_sum', 'score_geb_sum_norm', 'score_verhaeltnis', 'final_score'],
            'OUTPUT': final_path
        }, context=context, feedback=feedback)
        log_info(feedback, log_path, f"✅ Finale Bewertung mit reduzierten Attributen gespeichert: {final_path}")

        # Temporäre Dateien aufräumen
        for temp_file in [temp_joined_path, temp_final_path]:
            if os.path.exists(temp_file):
                try:
                    os.remove(temp_file)
                    log_info(feedback, log_path, f"🧹 Temporäre Datei gelöscht: {temp_file}")
                except Exception as e:
                    log_info(feedback, log_path, f"⚠️ Konnte temporäre Datei nicht löschen {temp_file}: {str(e)}")

        log_info(feedback, log_path, "🏁 Phase 4 abgeschlossen: Bewertungsskala erstellt")

    except Exception as e:
        log_info(feedback, log_path, f"❌ Fehler in Phase 4: {str(e)}")
        log_info(feedback, log_path, f"Stacktrace: {traceback.format_exc()}")
        raise QgsProcessingException(f"Fehler in Phase 4: {str(e)}")

class SuperblockDetectorBS(QgsProcessingAlgorithm):
    """
    QGIS Processing Algorithmus zur Detektion potenzieller Superblock-Standorte in Basel.
    
    Dieser Algorithmus implementiert die vollständige Verarbeitungskette für die
    Superblock-Analyse in der Stadt Basel. Er verarbeitet diverse Eingabedaten,
    bereinigt diese und erstellt eine bewertete Ausgabe möglicher Standorte.
    
    Die Verarbeitung erfolgt in vier Hauptphasen:
    1. Vorprozessierung: Transformation und Filterung der Eingabedaten
    2. Bereinigung und Segmentierung: Aufbereitung des Mobilitätsnetzes
    3. Geobasierte Analyse: Identifikation geeigneter Untersuchungsgebiete
    4. Quantilbasierte Bewertung: Erstellung einer Bewertungsskala
    """

    # Parameter-Definitionen für die QGIS-Benutzeroberfläche
    PARAMS = {
        "mobilitaetsnetz": "Mobilitätsnetz (Strassennetz)",
        "liniennetz_oev": "Liniennetz öffentlicher Verkehr",
        "teilrichtplan_velo": "Teilrichtplan Velo",
        "ausnahmetransporte": "Ausnahmetransportrouten",
        "notfallachsen": "Lifeline- und Notfallachsen",
        "gebaeude": "Gebäudeinformationen (kt) oder GWR-Daten (bund)",
        "liegenschaften": "Liegenschaftsflächen"
    }

    # Parameter-IDs für die interne Verarbeitung
    OUTPUT_FOLDER = "OUTPUT_FOLDER"
    SCORE_WEIGHTING = "SCORE_WEIGHTING"

    # Vordefinierte Gewichtungskombinationen für die Score-Berechnung
    WEIGHTING_OPTIONS = [
        "Gebäudescore 80% / Verhältnis-Score 20% (Standard)",
        "Gebäudescore 70% / Verhältnis-Score 30%",
        "Gebäudescore 60% / Verhältnis-Score 40%",
        "Gebäudescore 50% / Verhältnis-Score 50%",
        "Gebäudescore 40% / Verhältnis-Score 60%",
        "Gebäudescore 30% / Verhältnis-Score 70%",
        "Gebäudescore 20% / Verhältnis-Score 80%"
    ]

    def name(self) -> str:
        """Interner Name des Algorithmus für QGIS."""
        return "superblock_detector_bs"

    def displayName(self) -> str:
        """Anzeigename des Algorithmus in der QGIS-Benutzeroberfläche."""
        return "Superblock-Detector BS"

    def group(self) -> str:
        """Gruppe, in der der Algorithmus in QGIS erscheint."""
        return "Superblock-Analyse"

    def groupId(self) -> str:
        """Eindeutige ID der Algorithmus-Gruppe."""
        return "superblockanalyse"

    def shortHelpString(self) -> str:
        """
        Kurze Beschreibung des Algorithmus für die QGIS-Benutzeroberfläche.
        
        Diese Beschreibung wird in der QGIS-Hilfe und als Tooltip angezeigt.
        """
        return (
            "Dieses Skript analysiert potenzielle Superblock-Standorte in Basel.\n\n"
            "Es erstellt ein projektbasiertes Verzeichnis mit transformierten und "
            "gefilterten GPKG-Dateien. Die Verarbeitung erfolgt in vier Phasen:\n\n"
            "1. Vorprozessierung: Transformiert und filtert Eingabedaten\n"
            "2. Bereinigung und Segmentierung: Bereinigt das Mobilitätsnetz\n"
            "3. Geobasierte Analyse: Identifiziert geeignete Untersuchungsgebiete\n"
            "4. Quantilbasierte Bewertung: Erstellt eine Bewertungsskala\n\n"
            "Die Bewertung erfolgt durch zwei Scores:\n\n"
            "• Gebäudescore (-3 bis +3):\n"
            "  - Bewertet die Gebäudenutzung basierend auf Gebäudeklassen (GWR) oderGebäudekategorien (kantonal)\n"
            "  - +3: Sehr gut geeignet (z.B. Wohngebäude)\n"
            "  - -3: Sehr schlecht geeignet (z.B. Industriegebäude)\n\n"
            "• Verhältnis-Score (-3 bis +3):\n"
            "  - Bewertet die Form des Blocks\n"
            "  - +3: Optimales Verhältnis (nahe 1:1)\n"
            "  - -3: Ungünstiges Verhältnis (sehr länglich)\n\n"
            "Die Gewichtung der Scores kann über das Dropdown-Menü angepasst werden:\n"
            "• Höherer Gebäudescore: Fokus auf Gebäudenutzung\n"
            "• Höherer Verhältnis-Score: Fokus auf Blockform\n\n"
            "Die Ergebnisse werden in einem strukturierten Verzeichnis gespeichert."
        )

    def createInstance(self) -> QgsProcessingAlgorithm:
        """Erstellt eine neue Instanz des Algorithmus."""
        return SuperblockDetectorBS()

    def initAlgorithm(self, config=None) -> None:
        """
        Initialisiert die Parameter des Algorithmus.
        """
        # Eingabeparameter für Layer
        for param_id, label in self.PARAMS.items():
            self.addParameter(
                QgsProcessingParameterFeatureSource(
                    param_id,
                    label,
                    [QgsProcessing.TypeVectorAnyGeometry],
                    optional=False
                )
            )

        # Ausgabeparameter
        self.addParameter(
            QgsProcessingParameterFolderDestination(
                self.OUTPUT_FOLDER,
                "Zielverzeichnis für Arbeitsordner",
                defaultValue=""
            )
        )

        # Parameter für Score-Gewichtung als Dropdown
        weight_param = QgsProcessingParameterEnum(
            self.SCORE_WEIGHTING,
            "Score-Gewichtung",
            options=self.WEIGHTING_OPTIONS,
            defaultValue=0,
            optional=False
        )
        weight_param.setHelp(
            "Wählen Sie eine Gewichtungskombination für die Scores:\n\n"
            "• Gebäudescore (-3 bis +3):\n"
            "  - Bewertet die Gebäudenutzung (GWR oder kantonal)\n"
            "  - +3: Sehr gut geeignet (z.B. Wohngebäude)\n"
            "  - -3: Sehr schlecht geeignet (z.B. Industriegebäude)\n\n"
            "• Verhältnis-Score (-3 bis +3):\n"
            "  - Bewertet die Form des Blocks\n"
            "  - +3: Optimales Verhältnis (nahe 1:1)\n"
            "  - -3: Ungünstiges Verhältnis (sehr länglich)\n\n"
            "Die Summe beider Gewichtungen ergibt immer 100%.\n"
            "• Höherer Gebäudescore: Fokus auf Gebäudenutzung\n"
            "• Höherer Verhältnis-Score: Fokus auf Blockform"
        )
        self.addParameter(weight_param)

    def processAlgorithm(
        self,
        parameters: Dict[str, Any],
        context: QgsProcessingContext,
        feedback: QgsProcessingFeedback
    ) -> Dict[str, Any]:
        """
        Führt den Algorithmus aus.
        """
        # Validierung des Ausgabeverzeichnisses
        import_path = self.parameterAsString(parameters, self.OUTPUT_FOLDER, context)
        if not import_path or not os.path.isdir(import_path):
            raise QgsProcessingException("Es muss ein gültiges Zielverzeichnis angegeben werden")

        # Gewichtungen aus der Auswahl berechnen
        weight_index = self.parameterAsInt(parameters, self.SCORE_WEIGHTING, context)
        geb_weight = 80 - (weight_index * 10)  # 80, 70, 60, 50, 40, 30, 20
        verh_weight = 100 - geb_weight

        project_folder = None
        try:
            # 📁 Projektstruktur erstellen
            project_folder = create_unique_project_folder(import_path, feedback)
            log_path = os.path.join(project_folder, "prozess_log.txt")
            write_log_message(log_path, f"📁 Projektverzeichnis erstellt: {project_folder}")
            write_log_message(log_path, f"⚖️ Gewählte Score-Gewichtung: {self.WEIGHTING_OPTIONS[weight_index]}")

            prepared_input_path = os.path.join(project_folder, "_prepared_inputdata")
            temp_path = os.path.join(project_folder, "_tempdata")
            final_output_dir = os.path.join(project_folder, "_finaloutput")

            feedback.pushInfo(f"📄 Logging aktiv: {log_path}")
            write_log_message(log_path, f"📂 Unterordner: {prepared_input_path}, {temp_path}, {final_output_dir}")

            # Ergebnisstruktur
            result_paths = {}

            # 🔁 Eingabelayer transformieren
            layer_paths = {}
            for param_id in self.PARAMS.keys():
                layer = self.parameterAsVectorLayer(parameters, param_id, context)
                if not layer.isValid():
                    raise QgsProcessingException(f"Ungültiger Layer: {param_id}")
                    
                feedback.pushInfo(f"📥 Lade Layer: {param_id}")
                write_log_message(log_path, f"📥 Eingabe: {param_id} = {layer.name()}")
                layer_paths[param_id] = reproject_if_needed(layer, context, feedback, log_path)

            # ▶️ Phase 1: Vorprozessierung
            phase_1_vorprozessierung(
                layer_paths=layer_paths,
                prepared_input_path=prepared_input_path,
                context=context,
                feedback=feedback,
                log_path=log_path
            )

            # ▶️ Phase 2: Netz bereinigen & Segmentieren
            phase_2_bereinigung_segmentierung(
                prepared_input_path=prepared_input_path,
                temp_path=temp_path,
                final_output_dir=final_output_dir,
                log_info=log_info,
                log_path=log_path,
                context=context,
                feedback=feedback
            )

            # ▶️ Gebäudedatenquelle erkennen
            gebaeude_gwr_path = os.path.join(prepared_input_path, "gwr_bund_bs_lv95.gpkg")
            gebaeude_kt_path = os.path.join(prepared_input_path, "gebaeudeinformationen_kt_bs_lv95.gpkg")

            if os.path.exists(gebaeude_kt_path):
                gebaeude_path = gebaeude_kt_path
                feedback.pushInfo("📌 Verwende kantonale Gebäudedaten")
                write_log_message(log_path, "📌 Gebäudedaten: kantonal")
            elif os.path.exists(gebaeude_gwr_path):
                gebaeude_path = gebaeude_gwr_path
                feedback.pushInfo("📌 Verwende GWR-Gebäudedaten")
                write_log_message(log_path, "📌 Gebäudedaten: GWR")
            else:
                raise QgsProcessingException("Kein gültiger Gebäudedatensatz gefunden (weder GWR noch kantonal)")

            # Ergebnisstruktur ergänzen
            result_paths.update({
                "gebaeude": gebaeude_path,
                "liegenschaften": os.path.join(prepared_input_path, "liegenschaftsflaechen_lv95.gpkg"),
                "buffer": os.path.join(temp_path, "buffer_dissolved.gpkg")
            })

            # ▶️ Phase 3: Geobasierte Analyse
            phase_3_gebietsanalyse(
                final_path=os.path.join(final_output_dir, "mobilitaetsnetz_bereinigt.gpkg"),
                temp_path=temp_path,
                result_paths=result_paths,
                context=context,
                feedback=feedback,
                log_info=log_info,
                log_path=log_path
            )

            result_paths.update({
                "final_buffer": os.path.join(temp_path, "mobilitaetsnetz_finalbuffer.gpkg"),
                "singleparts": os.path.join(temp_path, "4_liegenschaftsflaechen_blocks.gpkg")
            })

            # ▶️ Phase 4: Quantilbasierte Bewertung & Verhältnis-Skala
            phase_4_quantilskala(
                temp_path=temp_path,
                final_output_dir=final_output_dir,
                context=context,
                feedback=feedback,
                log_info=log_info,
                log_path=log_path,
                geb_weight=geb_weight,
                verh_weight=verh_weight
            )

            result_paths.update({
                "scoring_joined": os.path.join(final_output_dir, "3_liegenschaftsflaechen_joined_scores.gpkg")
            })

            return {
                "PROJECT_FOLDER": project_folder,
                "LOG_FILE": log_path,
                "RESULT_SCORE_LAYER": result_paths["scoring_joined"]
            }

        except Exception as e:
            if project_folder and os.path.isdir(project_folder):
                shutil.rmtree(project_folder, ignore_errors=True)
                feedback.pushInfo(f"🧹 Projektordner gelöscht: {project_folder}")
            raise QgsProcessingException(f"Prozess abgebrochen: {str(e)}")
