#!/usr/bin/env python3
"""
Generate court sheet PNGs for ShipmentDeadlineCourt scenarios.

Each court sheet is 1200x900, split into:
  - Header:     InternetCourt branding + case ID + statement
  - Left panel: Contract summary (party names, deadline clause)
  - Right panel: Evidence document (ANB customs exit or SUNAT border gate)
  - Footer:     Generation timestamp + document label

Scenarios:
  A (TIMELY)       – exporter 22:41, importer 23:12 — both before 23:59 deadline
  B (LATE)         – exporter 02:15 next day, importer 02:47 next day — both after
  C (UNDETERMINED) – exporter 23:52 (8 min before), importer truck ref mismatch + illegible time

Usage:
  python3 scripts/generate_court_sheets.py
"""

import os
import json
from datetime import datetime, timezone
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont

REPO = Path(__file__).parent.parent
EVIDENCE = REPO / "evidence"

# ── Fonts ────────────────────────────────────────────────────────────────────
FONT_DIR = "/usr/share/fonts/truetype/dejavu"
UBUNTU_DIR = "/usr/share/fonts/truetype/ubuntu"

def font(size, bold=False, mono=False):
    if mono:
        return ImageFont.truetype(f"{FONT_DIR}/DejaVuSansMono.ttf", size)
    if bold:
        return ImageFont.truetype(f"{FONT_DIR}/DejaVuSans-Bold.ttf", size)
    return ImageFont.truetype(f"{FONT_DIR}/DejaVuSans.ttf", size)

# ── Palette ──────────────────────────────────────────────────────────────────
BG          = (252, 251, 249)   # warm white
HEADER_BG   = (18,  18,  18)   # near-black
HEADER_FG   = (255, 255, 255)
LEFT_BG     = (245, 243, 240)
RIGHT_BG    = (255, 255, 255)
DIVIDER     = (200, 196, 190)
FOOTER_BG   = (30,  30,  30)
FOOTER_FG   = (180, 180, 180)
LABEL_GREEN = (21,  128,  61)   # exporter (A)
LABEL_AMBER = (146,  64,  14)   # importer (B)
LABEL_GREY  = (75,  75,  75)
ACCENT_IC   = (99,  60, 180)    # InternetCourt purple
TIMELY_G    = (22, 101,  52)
LATE_R      = (185,  28,  28)
UND_Y       = (146,  64,  14)

# Document org colours
ANB_GREEN   = (1,   94,  67)    # Aduana Nacional Bolivia
ANB_YELLOW  = (252, 196,  0)
SUNAT_RED   = (185,  28,  28)
SUNAT_DARK  = (60,   10,  10)

# ── Layout constants ─────────────────────────────────────────────────────────
W, H        = 1200, 900
HEADER_H    = 110
FOOTER_H    = 36
PANEL_Y     = HEADER_Y = HEADER_H
PANEL_H     = H - HEADER_H - FOOTER_H
LEFT_W      = 390
RIGHT_X     = LEFT_W + 1
RIGHT_W     = W - RIGHT_X
MARGIN      = 20

# ── Drawing helpers ──────────────────────────────────────────────────────────

def new_image():
    img = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(img)
    return img, d

def fill_rect(d, x, y, w, h, color):
    d.rectangle([x, y, x+w, y+h], fill=color)

def hline(d, x, y, w, color=DIVIDER, th=1):
    d.rectangle([x, y, x+w, y+th], fill=color)

def vline(d, x, y, h, color=DIVIDER, th=1):
    d.rectangle([x, y, x+th, y+h], fill=color)

def text(d, x, y, s, fnt, color=(30,30,30), anchor="la"):
    d.text((x, y), s, font=fnt, fill=color, anchor=anchor)

def wrapped_text(d, x, y, s, fnt, color, max_w, line_h=18):
    """Very simple word-wrap."""
    words = s.split()
    line = ""
    cy = y
    for w in words:
        test = (line + " " + w).strip()
        bb = d.textbbox((0,0), test, font=fnt)
        if bb[2] - bb[0] > max_w and line:
            d.text((x, cy), line, font=fnt, fill=color)
            cy += line_h
            line = w
        else:
            line = test
    if line:
        d.text((x, cy), line, font=fnt, fill=color)
    return cy + line_h

def badge(d, x, y, label, bg, fg=(255,255,255)):
    f = font(10, bold=True)
    bb = d.textbbox((0,0), label, font=f)
    pw, ph = bb[2]-bb[0]+12, bb[3]-bb[1]+6
    d.rounded_rectangle([x, y, x+pw, y+ph], radius=3, fill=bg)
    d.text((x+6, y+3), label, font=f, fill=fg)
    return pw

# ── HEADER ───────────────────────────────────────────────────────────────────

def draw_header(d, case_id, statement, deadline):
    fill_rect(d, 0, 0, W, HEADER_H, HEADER_BG)

    # IC logo stripe
    fill_rect(d, 0, 0, 6, HEADER_H, ACCENT_IC)

    # Logo text
    text(d, 18, 14, "INTERNET", font(9, bold=True), ACCENT_IC)
    text(d, 18, 26, "COURT", font(9, bold=True), (255,255,255))

    # Case ID
    text(d, 18, 44, f"Case {case_id}", font(11, bold=True), (210,210,210))
    text(d, 18, 62, "SHIPMENT DEADLINE EVALUATION", font(9), (140,140,140))

    # Statement (truncated)
    stmt_short = statement if len(statement) < 110 else statement[:107] + "…"
    text(d, 18, 82, stmt_short, font(9), (190,190,190))

    # Deadline pill top-right
    dl_label = f"Deadline: {deadline}"
    f14 = font(12, bold=True)
    bb = d.textbbox((0,0), dl_label, font=f14)
    tw = bb[2]-bb[0]
    text(d, W - tw - 20, 20, dl_label, f14, (255, 220, 80))

    # Guideline version
    text(d, W - 200, 46, "Guideline: shipment-deadline-v1", font(9), (120,120,120))

# ── LEFT PANEL: contract summary ─────────────────────────────────────────────

def draw_left(d, case_id, contract_no, exporter, importer, goods,
              deadline, clause):
    x0, y0, pw = MARGIN, PANEL_Y + MARGIN, LEFT_W - 2*MARGIN
    fill_rect(d, 0, PANEL_Y, LEFT_W, PANEL_H, LEFT_BG)
    vline(d, LEFT_W, PANEL_Y, PANEL_H, DIVIDER, 1)

    cy = y0

    # Section title
    text(d, x0, cy, "CONTRACT SUMMARY", font(9, bold=True), LABEL_GREY)
    cy += 16
    hline(d, x0, cy, pw, DIVIDER)
    cy += 8

    def kv(label, value, vc=(40,40,40)):
        nonlocal cy
        text(d, x0, cy, label, font(9, bold=True), (110,110,110))
        cy += 13
        wrapped_text(d, x0, cy, value, font(10), vc, pw, 15)
        cy += 18

    kv("Contract No.", contract_no)
    kv("Exporter (Party A)", exporter, TIMELY_G)
    kv("Importer (Party B)", importer, LATE_R)
    kv("Goods", goods)

    cy += 4
    hline(d, x0, cy, pw, DIVIDER)
    cy += 10

    text(d, x0, cy, "DEADLINE CLAUSE", font(9, bold=True), LABEL_GREY)
    cy += 16
    wrapped_text(d, x0, cy, clause, font(9), (60,60,60), pw, 14)
    cy += 42

    hline(d, x0, cy, pw, DIVIDER)
    cy += 10

    text(d, x0, cy, "DEADLINE (local time)", font(9, bold=True), LABEL_GREY)
    cy += 16
    text(d, x0, cy, deadline, font(13, bold=True), (30,30,30))
    cy += 22
    text(d, x0, cy, "UTC-4 (Bolivia Standard Time)", font(9), (110,110,110))
    cy += 22

    hline(d, x0, cy, pw, DIVIDER)
    cy += 10
    text(d, x0, cy, "CASE ID", font(9, bold=True), LABEL_GREY)
    cy += 14
    text(d, x0, cy, case_id, font(10, mono=True), (60,60,60))
    cy += 18
    text(d, x0, cy, "Statement hash: sha256", font(8), (140,140,140))

# ── RIGHT PANEL: ANB customs exit ────────────────────────────────────────────

def draw_anb_exit(d, due_no, timestamp_str, exporter, containers,
                  checkpoint_officer, truck_plate, status, status_color):
    """Bolivian customs exit certificate (exporter document)."""
    x0 = RIGHT_X + MARGIN
    y0 = PANEL_Y + MARGIN
    pw = RIGHT_W - 2*MARGIN

    fill_rect(d, RIGHT_X, PANEL_Y, RIGHT_W, PANEL_H, RIGHT_BG)

    # Org header bar
    fill_rect(d, RIGHT_X, PANEL_Y, RIGHT_W, 52, ANB_GREEN)
    fill_rect(d, RIGHT_X, PANEL_Y + 52, RIGHT_W, 5, ANB_YELLOW)

    text(d, x0, PANEL_Y + 8, "ADUANA NACIONAL DE BOLIVIA", font(14, bold=True), (255,255,255))
    text(d, x0, PANEL_Y + 28, "DESPACHO DE EXPORTACIÓN — CONTROL FRONTERIZO", font(9), (200,240,210))

    # Doc label badge
    text(d, W - 220, PANEL_Y + 10, "EXPORTER EVIDENCE", font(9, bold=True), ANB_YELLOW)
    text(d, W - 220, PANEL_Y + 27, "Customs Exit Record", font(9), (200,240,210))

    cy = PANEL_Y + 68

    # DUE reference
    fill_rect(d, x0, cy, pw, 28, (240, 248, 242))
    d.rectangle([x0, cy, x0+pw, cy+28], outline=ANB_GREEN, width=1)
    text(d, x0+8, cy+7, "DUE No.:", font(9, bold=True), ANB_GREEN)
    text(d, x0+80, cy+7, due_no, font(11, bold=True, mono=True), (20,20,20))
    text(d, x0+pw-100, cy+7, "ORIGINAL", font(9, bold=True), ANB_GREEN)
    cy += 36

    # Timestamp — the key fact
    fill_rect(d, x0, cy, pw, 44, (248, 255, 250))
    d.rectangle([x0, cy, x0+pw, cy+44], outline=status_color, width=2)
    text(d, x0+8, cy+6, "FECHA Y HORA DE DESPACHO ADUANERO:", font(9, bold=True), (80,80,80))
    text(d, x0+8, cy+22, timestamp_str, font(18, bold=True, mono=True), status_color)
    # Disambiguate month in plain text to avoid AI vision misreads (e.g. 04→05)
    import re as _re
    _m = _re.search(r"2026-(\d{2})-(\d{2})", timestamp_str)
    if _m:
        _months = ["","JAN","FEB","MAR","APR","MAY","JUN","JUL","AUG","SEP","OCT","NOV","DEC"]
        _month_abbr = _months[int(_m.group(1))] if 1 <= int(_m.group(1)) <= 12 else ""
        _day = _m.group(2)
        text(d, x0+pw-130, cy+22, f"{_day} {_month_abbr} 2026", font(12, bold=True), (100,100,100))
    cy += 52

    # Fields grid
    def field(label, value, vc=(30,30,30)):
        nonlocal cy
        text(d, x0, cy, label.upper(), font(8, bold=True), (120,120,120))
        cy += 12
        text(d, x0, cy, value, font(10), vc)
        cy += 18

    field("Exportador / Shipper", exporter)
    field("Punto de Control", "Aduana Desaguadero — Código 1140")
    field("Checkpoint Officer", checkpoint_officer)
    field("Vehículo / Truck", truck_plate)
    field("Contenedores", containers, (60,60,60))

    cy += 4
    hline(d, x0, cy, pw, DIVIDER)
    cy += 8

    # Status stamp
    fill_rect(d, x0, cy, pw, 32, (240,255,244) if status_color == TIMELY_G else (255,242,242))
    d.rectangle([x0, cy, x0+pw, cy+32], outline=status_color, width=2)
    d.rounded_rectangle([x0+pw-120, cy+6, x0+pw-8, cy+26], radius=4, fill=status_color)
    text(d, x0+pw-114, cy+11, status, font(10, bold=True), (255,255,255))
    text(d, x0+8, cy+10, "Estado del despacho:", font(9, bold=True), (80,80,80))
    cy += 40

    # Signature block
    cy += 6
    text(d, x0, cy, "Firma del funcionario / Officer signature:", font(9), (120,120,120))
    cy += 14
    hline(d, x0, cy, 180, (80,80,80))
    cy += 6
    text(d, x0, cy, checkpoint_officer, font(9, bold=True), (40,40,40))
    text(d, x0+220, cy-10, "Sello oficial:", font(9), (120,120,120))
    # Simulated stamp circle
    d.ellipse([x0+310, cy-18, x0+380, cy+20], outline=ANB_GREEN, width=2)
    d.ellipse([x0+316, cy-12, x0+374, cy+14], outline=ANB_GREEN, width=1)
    text(d, x0+325, cy-5, "ANB", font(9, bold=True), ANB_GREEN)
    text(d, x0+320, cy+4, "1140", font(8), ANB_GREEN)

# ── RIGHT PANEL: SUNAT border gate ───────────────────────────────────────────

def draw_sunat_gate(d, event_id, timestamp_str, consignee, containers,
                    checkpoint_officer, truck_plate, timestamp_note=None):
    """Peruvian border gate event record (importer document)."""
    x0 = RIGHT_X + MARGIN
    y0 = PANEL_Y + MARGIN
    pw = RIGHT_W - 2*MARGIN

    fill_rect(d, RIGHT_X, PANEL_Y, RIGHT_W, PANEL_H, RIGHT_BG)

    # Org header bar
    fill_rect(d, RIGHT_X, PANEL_Y, RIGHT_W, 52, SUNAT_RED)
    fill_rect(d, RIGHT_X, PANEL_Y + 52, RIGHT_W, 4, (220, 160, 0))

    text(d, x0, PANEL_Y + 6, "SUNAT — SUPERINTENDENCIA NACIONAL DE ADUANAS", font(12, bold=True), (255,255,255))
    text(d, x0, PANEL_Y + 24, "CONTROL FRONTERIZO DESAGUADERO — REGISTRO DE EVENTO DE INGRESO", font(9), (255,200,200))
    text(d, x0, PANEL_Y + 38, "Puesto de Control Fronterizo Desaguadero, Puno, Perú", font(8), (255,180,180))

    # Badge
    text(d, W - 230, PANEL_Y + 10, "IMPORTER EVIDENCE", font(9, bold=True), (255,220,80))
    text(d, W - 230, PANEL_Y + 27, "Border Gate Event Record", font(9), (255,200,200))

    cy = PANEL_Y + 68

    # Event reference
    fill_rect(d, x0, cy, pw, 28, (255, 245, 245))
    d.rectangle([x0, cy, x0+pw, cy+28], outline=SUNAT_RED, width=1)
    text(d, x0+8, cy+7, "Evento ID:", font(9, bold=True), SUNAT_RED)
    text(d, x0+80, cy+7, event_id, font(11, bold=True, mono=True), (20,20,20))
    text(d, x0+pw-110, cy+7, "REGISTRO OFICIAL", font(9, bold=True), SUNAT_RED)
    cy += 36

    # Timestamp — key fact
    if timestamp_str:
        ts_color = LATE_R if "2026-04-06" in timestamp_str else TIMELY_G
        # Disambiguate month in plain text to avoid AI vision misreads
        import re as _re2
        _m2 = _re2.search(r"2026-(\d{2})-(\d{2})", timestamp_str)
        _sunat_month_label = ""
        if _m2:
            _months2 = ["","JAN","FEB","MAR","APR","MAY","JUN","JUL","AUG","SEP","OCT","NOV","DEC"]
            _sunat_month_label = f"{_m2.group(2)} {_months2[int(_m2.group(1))] if 1 <= int(_m2.group(1)) <= 12 else ''} 2026"
        fill_rect(d, x0, cy, pw, 44, (255, 248, 248))
        d.rectangle([x0, cy, x0+pw, cy+44], outline=ts_color, width=2)
        text(d, x0+8, cy+6, "FECHA Y HORA DE CRUCE DE FRONTERA (GATE EVENT):", font(9, bold=True), (80,80,80))
        text(d, x0+8, cy+22, timestamp_str, font(18, bold=True, mono=True), ts_color)
        if _sunat_month_label:
            text(d, x0+pw-130, cy+22, _sunat_month_label, font(12, bold=True), (100,100,100))
        if timestamp_note:
            text(d, x0+8+320, cy+28, timestamp_note, font(8), (150,80,80))
    else:
        # Illegible / missing timestamp
        fill_rect(d, x0, cy, pw, 44, (255, 250, 230))
        d.rectangle([x0, cy, x0+pw, cy+44], outline=UND_Y, width=2)
        text(d, x0+8, cy+6, "FECHA Y HORA DE CRUCE DE FRONTERA (GATE EVENT):", font(9, bold=True), (80,80,80))
        # Simulate illegible ink
        text(d, x0+8, cy+20, "2026-04-05   __:__:__ -04:00", font(18, bold=True, mono=True), (180,180,180))
        text(d, x0+8, cy+38, "⚠ HORA ILEGIBLE — tinta deteriorada / time field degraded", font(8, bold=True), UND_Y)
    cy += 52

    def field(label, value, vc=(30,30,30)):
        nonlocal cy
        text(d, x0, cy, label.upper(), font(8, bold=True), (120,120,120))
        cy += 12
        text(d, x0, cy, value, font(10), vc)
        cy += 18

    field("Consignatario / Importer", consignee)
    field("Puesto de Control", "Frontera Desaguadero — PCF-PUN-001")
    field("Funcionario SUNAT", checkpoint_officer)
    field("Placa / Truck plate", truck_plate)
    field("Referencias de contenedor", containers, (60,60,60))

    cy += 4
    hline(d, x0, cy, pw, DIVIDER)
    cy += 8

    # Auth block
    fill_rect(d, x0, cy, pw, 32, (255, 245, 245))
    d.rectangle([x0, cy, x0+pw, cy+32], outline=SUNAT_RED, width=1)
    text(d, x0+8, cy+10, "Registro generado por sistema SIGAD-PCF.", font(9), (80,80,80))
    text(d, x0+8, cy+22, "Autenticidad verificable en: sunat.gob.pe/pcf-consulta", font(8), (120,120,120))
    cy += 40

    # Signature block
    cy += 6
    text(d, x0, cy, "Firma del funcionario / Officer signature:", font(9), (120,120,120))
    cy += 14
    hline(d, x0, cy, 180, (80,80,80))
    cy += 6
    text(d, x0, cy, checkpoint_officer, font(9, bold=True), (40,40,40))
    # Stamp
    d.ellipse([x0+220, cy-18, x0+295, cy+20], outline=SUNAT_RED, width=2)
    d.ellipse([x0+226, cy-12, x0+289, cy+14], outline=SUNAT_RED, width=1)
    text(d, x0+233, cy-5, "SUNAT", font(8, bold=True), SUNAT_RED)
    text(d, x0+235, cy+4, "PCF-PUN", font(7), SUNAT_RED)

# ── FOOTER ───────────────────────────────────────────────────────────────────

def draw_footer(d, doc_type, filename):
    fy = H - FOOTER_H
    fill_rect(d, 0, fy, W, FOOTER_H, FOOTER_BG)
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
    text(d, 12, fy + 10, f"Generated: {now} | InternetCourt Case Exhibit | {doc_type}", font(8), FOOTER_FG)
    text(d, W - 200, fy + 10, filename, font(8, mono=True), FOOTER_FG)

# ── SCENARIO DEFINITIONS ─────────────────────────────────────────────────────

CASE_META = {
    "qc-coop-2026-0003": {
        "case_id": "qc-coop-2026-0003",
        "contract_no": "ISPA-2025-BOL-PER-0047",
        "exporter": "Minera Andina SRL — Potosí, Bolivia",
        "importer": "Electroquímica del Perú S.A. — Lima, Peru",
        "goods": "50 MT battery-grade lithium carbonate (Li₂CO₃), ISO 6206",
        "deadline": "2026-04-05T23:59:59-04:00",
        "deadline_short": "2026-04-05 23:59 -04:00",
        "clause": ("Art. 7.2 — Goods shall cross Bolivian export customs "
                   "at Desaguadero no later than 2026-04-05 23:59:59 BOT (UTC-4)."),
        "statement": ("Shipment under Contract ISPA-2025-BOL-PER-0047 crossed "
                      "Bolivian export customs at Desaguadero on or before 2026-04-05T23:59:59-04:00."),
    },
    "qc-coop-2026-0004": {
        "case_id": "qc-coop-2026-0004",
        "contract_no": "ISPA-2025-BOL-PER-0047",
        "exporter": "Minera Andina SRL — Potosí, Bolivia",
        "importer": "Electroquímica del Perú S.A. — Lima, Peru",
        "goods": "50 MT battery-grade lithium carbonate (Li₂CO₃), ISO 6206",
        "deadline": "2026-04-05T23:59:59-04:00",
        "deadline_short": "2026-04-05 23:59 -04:00",
        "clause": ("Art. 7.2 — Goods shall cross Bolivian export customs "
                   "at Desaguadero no later than 2026-04-05 23:59:59 BOT (UTC-4)."),
        "statement": ("Shipment under Contract ISPA-2025-BOL-PER-0047 crossed "
                      "Bolivian export customs at Desaguadero on or before 2026-04-05T23:59:59-04:00."),
    },
    "qc-coop-2026-0005": {
        "case_id": "qc-coop-2026-0005",
        "contract_no": "ISPA-2025-BOL-PER-0047",
        "exporter": "Minera Andina SRL — Potosí, Bolivia",
        "importer": "Electroquímica del Perú S.A. — Lima, Peru",
        "goods": "50 MT battery-grade lithium carbonate (Li₂CO₃), ISO 6206",
        "deadline": "2026-04-05T23:59:59-04:00",
        "deadline_short": "2026-04-05 23:59 -04:00",
        "clause": ("Art. 7.2 — Goods shall cross Bolivian export customs "
                   "at Desaguadero no later than 2026-04-05 23:59:59 BOT (UTC-4)."),
        "statement": ("Shipment under Contract ISPA-2025-BOL-PER-0047 crossed "
                      "Bolivian export customs at Desaguadero on or before 2026-04-05T23:59:59-04:00."),
    },
}

SCENARIOS = {
    # ── Scenario A: TIMELY ────────────────────────────────────────────────────
    "qc-coop-2026-0003": {
        "a": {
            "doc": "anb",
            "due_no": "DUE-2026-DES-0047821",
            "timestamp": "2026-04-05  22:41:00  -04:00",
            "exporter": "Minera Andina SRL — Tax ID: 1029384756",
            "containers": "COSCU-123456-7 / COSCU-123457-5 / COSCU-123458-3 / COSCU-123459-1",
            "officer": "Lic. Ramiro Chávez Apaza — Funcionario ANB 4420",
            "truck": "Placa: 3456-BPY | Tractocamión Mercedes Actros",
            "status": "DESPACHO ACEPTADO",
            "status_color": TIMELY_G,
        },
        "b": {
            "doc": "sunat",
            "event_id": "PCF-PUN-2026-04-05-0914",
            "timestamp": "2026-04-05  23:12:00  -04:00",
            "consignee": "Electroquímica del Perú S.A. — RUC: 20512345678",
            "containers": "COSCU-123456-7 / COSCU-123457-5 / COSCU-123458-3 / COSCU-123459-1",
            "officer": "Insp. Carmen Quispe Flores — SUNAT PCF-001",
            "truck": "Placa: 3456-BPY | Verificado en puesto",
            "timestamp_note": None,
        },
    },
    # ── Scenario B: LATE ─────────────────────────────────────────────────────
    "qc-coop-2026-0004": {
        "a": {
            "doc": "anb",
            "due_no": "DUE-2026-DES-0048103",
            "timestamp": "2026-04-06  02:15:00  -04:00",
            "exporter": "Minera Andina SRL — Tax ID: 1029384756",
            "containers": "COSCU-223456-2 / COSCU-223457-0 / COSCU-223458-8 / COSCU-223459-6",
            "officer": "Lic. Ramiro Chávez Apaza — Funcionario ANB 4420",
            "truck": "Placa: 7812-CPZ | Tractocamión Volvo FH",
            "status": "DESPACHO ACEPTADO — FUERA DE PLAZO",
            "status_color": LATE_R,
        },
        "b": {
            "doc": "sunat",
            "event_id": "PCF-PUN-2026-04-06-0038",
            "timestamp": "2026-04-06  02:47:00  -04:00",
            "consignee": "Electroquímica del Perú S.A. — RUC: 20512345678",
            "containers": "COSCU-223456-2 / COSCU-223457-0 / COSCU-223458-8 / COSCU-223459-6",
            "officer": "Insp. Marco Ticona Ramos — SUNAT PCF-001",
            "truck": "Placa: 7812-CPZ | Verificado en puesto",
            "timestamp_note": None,
        },
    },
    # ── Scenario C: UNDETERMINED ─────────────────────────────────────────────
    "qc-coop-2026-0005": {
        "a": {
            "doc": "anb",
            "due_no": "DUE-2026-DES-0047998",
            "timestamp": "2026-04-05  23:52:00  -04:00",
            "exporter": "Minera Andina SRL — Tax ID: 1029384756",
            "containers": "COSCU-323456-7 / COSCU-323457-5 / COSCU-323458-3 / COSCU-323459-1",
            "officer": "Lic. Ramiro Chávez Apaza — Funcionario ANB 4420",
            "truck": "Placa: 2291-AKL | Tractocamión Scania R500",
            "status": "DESPACHO ACEPTADO",
            "status_color": TIMELY_G,
        },
        "b": {
            "doc": "sunat",
            "event_id": "PCF-PUN-2026-04-05-0961",
            # timestamp=None triggers the illegible rendering
            "timestamp": None,
            "consignee": "Electroquímica del Perú S.A. — RUC: 20512345678",
            # Truck plate deliberately mismatched
            "containers": "⚠ Ref: COSCU-323456-7 — Placa camión no coincide con DUE",
            "officer": "Insp. Rosa Mamani Condori — SUNAT PCF-001",
            "truck": "Placa registrada: 8834-FMX | ≠ placa ANB 2291-AKL",
            "timestamp_note": None,
        },
    },
}

# ── MAIN GENERATOR ───────────────────────────────────────────────────────────

def generate_sheet(case_id, side):
    """Generate one court sheet PNG. side = 'a' or 'b'."""
    meta = CASE_META[case_id]
    scen = SCENARIOS[case_id][side]
    filename = f"court_sheet_{side}.png"

    img, d = new_image()

    draw_header(d, meta["case_id"], meta["statement"], meta["deadline_short"])

    draw_left(d,
        case_id=meta["case_id"],
        contract_no=meta["contract_no"],
        exporter=meta["exporter"],
        importer=meta["importer"],
        goods=meta["goods"],
        deadline=meta["deadline_short"],
        clause=meta["clause"],
    )

    if scen["doc"] == "anb":
        draw_anb_exit(d,
            due_no=scen["due_no"],
            timestamp_str=scen["timestamp"],
            exporter=scen["exporter"],
            containers=scen["containers"],
            checkpoint_officer=scen["officer"],
            truck_plate=scen["truck"],
            status=scen["status"],
            status_color=scen["status_color"],
        )
    else:
        draw_sunat_gate(d,
            event_id=scen["event_id"],
            timestamp_str=scen["timestamp"],
            consignee=scen["consignee"],
            containers=scen["containers"],
            checkpoint_officer=scen["officer"],
            truck_plate=scen["truck"],
            timestamp_note=scen.get("timestamp_note"),
        )

    draw_footer(d, "Exporter Evidence" if side == "a" else "Importer Evidence", filename)

    out = EVIDENCE / case_id / filename
    img.save(str(out), "PNG", dpi=(150, 150))
    print(f"  ✅ {out}")
    return str(out)


if __name__ == "__main__":
    print("Generating court sheet images...")
    generated = []
    for case_id in ["qc-coop-2026-0003", "qc-coop-2026-0004", "qc-coop-2026-0005"]:
        print(f"\n  Case {case_id}:")
        generated.append(generate_sheet(case_id, "a"))
        generated.append(generate_sheet(case_id, "b"))
    print(f"\n✅ {len(generated)} court sheets generated.")
