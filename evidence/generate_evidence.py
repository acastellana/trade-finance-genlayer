#!/usr/bin/env python3.12
"""Generate realistic trade finance dispute evidence PDFs.

Scenario: Bolivia → Peru lithium carbonate export quality dispute.
- Exporter: Minera Andina SRL (Potosí, Bolivia)
- Importer: Electroquímica del Perú S.A. (Lima, Peru)
- Goods: 50 MT battery-grade Li₂CO₃, ISO 6206 certified
- Case #17: Purity 99.71% (SGS exporter lab) vs 98.82% (BV importer lab) → UNDETERMINED
- Case #19: Purity 99.71% (SGS exporter lab) vs 94.2% (BV importer lab) → PARTY_B (clear fail)
- Forex rate: 1 BOB = 0.4948 PEN (March 2026, Banco Central de Reserva del Perú)

Cross-reference anchor data (Case #17 / primary shipment):
  PO:          EP-PO-2026-0178
  Lot:         MA-LOT-2026-0047
  SGS CoA:     CL-ANT-2026-04871 (dated 2026-01-15)
  SGS PSI:     CL-ANT-PSI-2026-01203 (dated 2026-01-22)
  B/L:         COSU-BOL-2026-001847 (vessel MV COSCO ATACAMA, sailed 2026-01-24)
  Containers:  COSCU-123456-7 (seal SGS-CL-880214), COSCU-123457-5 (seal SGS-CL-880215),
               COSCU-123458-3 (seal SGS-CL-880216), COSCU-123459-1 (seal SGS-CL-880217)
  BV report:   BV-LIM-2026-AN-00412 (dated 2026-02-12) → 98.82% blended
  EP arrival:  EP-QC-INS-2026-0089 (dated 2026-02-08)
  Rejection:   dated 2026-02-14

Cross-reference anchor data (Case #19 shipment):
  PO:          EP-PO-2026-0219
  Lot:         MA-LOT-2026-0119
  B/L:         COSU-BOL-2026-003311 (vessel MV COSCO PACIFIC, sailed 2026-02-18)
  BV report:   BV-LIM-2026-AN-00618 (dated 2026-03-15) → 94.2% purity
"""

import os
from reportlab.lib.pagesizes import letter, A4
from reportlab.lib.units import inch, mm, cm
from reportlab.lib.colors import (
    HexColor, black, white, grey, red, darkblue, navy,
    lightgrey, Color
)
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.enums import TA_LEFT, TA_CENTER, TA_RIGHT, TA_JUSTIFY
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle,
    PageBreak, HRFlowable, Frame, PageTemplate, BaseDocTemplate
)
from reportlab.pdfgen import canvas
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont

OUT = os.path.dirname(os.path.abspath(__file__))

# ─── Color palettes for different organizations ───
SGS_RED = HexColor("#E30613")
SGS_DARK = HexColor("#1A1A1A")
BV_ORANGE = HexColor("#FF6600")
BV_DARK = HexColor("#003366")
COSCO_BLUE = HexColor("#003DA5")
COSCO_DARK = HexColor("#001F52")
MINERA_GREEN = HexColor("#2E7D32")
ELECTRO_NAVY = HexColor("#0D1B2A")
ELECTRO_GOLD = HexColor("#C9A227")
ICC_BLUE = HexColor("#002D72")

styles = getSampleStyleSheet()


def make_style(name, parent='Normal', **kw):
    base = styles[parent]
    return ParagraphStyle(name, parent=base, **kw)


# Common styles
title_style = make_style('DocTitle', 'Title', fontSize=16, spaceAfter=6)
heading_style = make_style('DocHeading', 'Heading2', fontSize=12, spaceAfter=4)
body_style = make_style('DocBody', fontSize=10, leading=14, alignment=TA_JUSTIFY)
small_style = make_style('DocSmall', fontSize=8, leading=10, textColor=grey)
bold_style = make_style('DocBold', fontSize=10, leading=14)
right_style = make_style('DocRight', fontSize=10, alignment=TA_RIGHT)
center_style = make_style('DocCenter', fontSize=10, alignment=TA_CENTER)


def colored_header_bar(c_canvas, doc, color, text, subtitle=""):
    """Draw a colored header bar at the top of the page."""
    w, h = doc.pagesize
    c_canvas.saveState()
    c_canvas.setFillColor(color)
    c_canvas.rect(0, h - 80, w, 80, fill=1, stroke=0)
    c_canvas.setFillColor(white)
    c_canvas.setFont("Helvetica-Bold", 18)
    c_canvas.drawString(60, h - 45, text)
    if subtitle:
        c_canvas.setFont("Helvetica", 10)
        c_canvas.drawString(60, h - 62, subtitle)
    c_canvas.restoreState()


def footer(c_canvas, doc, text):
    w, h = doc.pagesize
    c_canvas.saveState()
    c_canvas.setFont("Helvetica", 7)
    c_canvas.setFillColor(grey)
    c_canvas.drawString(60, 30, text)
    c_canvas.drawRightString(w - 60, 30, f"Page {doc.page}")
    c_canvas.restoreState()


# ═══════════════════════════════════════════════════════════════════
# 1. SGS Certificate of Analysis (Exporter Evidence)
# ═══════════════════════════════════════════════════════════════════

def gen_sgs_coa():
    path = os.path.join(OUT, "01_SGS_Certificate_of_Analysis.pdf")
    doc = SimpleDocTemplate(path, pagesize=A4,
                            topMargin=100, bottomMargin=60,
                            leftMargin=50, rightMargin=50)

    def first_page(c, d):
        colored_header_bar(c, d, SGS_RED, "SGS", "When You Need To Be Sure")
        # Reference box
        w = d.pagesize[0]
        c.saveState()
        c.setStrokeColor(SGS_RED)
        c.setLineWidth(1.5)
        c.rect(w - 220, d.pagesize[1] - 130, 180, 40, fill=0)
        c.setFont("Helvetica-Bold", 9)
        c.setFillColor(SGS_DARK)
        c.drawString(w - 212, d.pagesize[1] - 113, "Report No: CL-ANT-2026-04871")
        c.drawString(w - 212, d.pagesize[1] - 126, "Date: 2026-01-15")
        c.restoreState()
        footer(c, d, "SGS Chile Ltda. | Av. Andrés Bello 2233, Providencia, Santiago | RUT: 96.722.460-K | www.sgs.cl")

    story = []
    story.append(Spacer(1, 40))

    story.append(Paragraph("<b>CERTIFICATE OF ANALYSIS</b>", make_style('t', fontSize=14, alignment=TA_CENTER, textColor=SGS_RED)))
    story.append(Spacer(1, 6))
    story.append(Paragraph("Lithium Carbonate (Li<sub>2</sub>CO<sub>3</sub>) — Battery Grade", make_style('t', fontSize=11, alignment=TA_CENTER)))
    story.append(Spacer(1, 20))

    # Client info
    info_data = [
        ["Client:", "Minera Andina SRL", "Sample ID:", "MA-LI50-2026-A"],
        ["Address:", "Zona Industrial km 12, Potosí, Bolivia", "Lot Number:", "MA-LOT-2026-0047"],
        ["Contact:", "Ing. Carlos Quispe Mamani", "Date Received:", "2026-01-10"],
        ["Purchase Order:", "EP-PO-2026-0178", "Date Tested:", "2026-01-12 to 2026-01-14"],
    ]
    t = Table(info_data, colWidths=[80, 190, 80, 140])
    t.setStyle(TableStyle([
        ('FONTNAME', (0, 0), (-1, -1), 'Helvetica'),
        ('FONTSIZE', (0, 0), (-1, -1), 9),
        ('FONTNAME', (0, 0), (0, -1), 'Helvetica-Bold'),
        ('FONTNAME', (2, 0), (2, -1), 'Helvetica-Bold'),
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
    ]))
    story.append(t)
    story.append(Spacer(1, 16))

    story.append(Paragraph("<b>TEST METHODOLOGY</b>", heading_style))
    story.append(Paragraph(
        "Analysis performed per ISO 6206:2023 — <i>Lithium carbonate for industrial use — "
        "Determination of lithium carbonate content</i>. Primary method: ICP-OES (Inductively "
        "Coupled Plasma Optical Emission Spectrometry) using Thermo Scientific iCAP 7400 Duo. "
        "Supplementary: volumetric titration per ASTM D3875. Moisture by Karl Fischer titration. "
        "All analyses performed in SGS Chile's ISO/IEC 17025:2017 accredited laboratory "
        "(Accreditation No. LE-1247).", body_style))
    story.append(Spacer(1, 12))

    story.append(Paragraph("<b>RESULTS</b>", heading_style))

    results = [
        ["Parameter", "Method", "Result", "Specification\n(ISO 6206 BG)", "Status"],
        ["Li₂CO₃ purity", "ICP-OES / ISO 6206", "99.71%", "≥ 99.0%", "PASS"],
        ["Na content", "ICP-OES", "0.018%", "≤ 0.04%", "PASS"],
        ["Ca content", "ICP-OES", "0.008%", "≤ 0.02%", "PASS"],
        ["Mg content", "ICP-OES", "0.005%", "≤ 0.01%", "PASS"],
        ["Fe content", "ICP-OES", "12 ppm", "≤ 20 ppm", "PASS"],
        ["Cl content", "Ion chromatography", "0.003%", "≤ 0.01%", "PASS"],
        ["SO₄ content", "Ion chromatography", "0.021%", "≤ 0.05%", "PASS"],
        ["Moisture (H₂O)", "Karl Fischer", "0.15%", "≤ 0.30%", "PASS"],
        ["Insoluble matter", "Gravimetric", "0.006%", "≤ 0.02%", "PASS"],
        ["D50 particle size", "Laser diffraction", "127 μm", "100–200 μm", "PASS"],
    ]

    t = Table(results, colWidths=[95, 100, 70, 90, 50])
    t.setStyle(TableStyle([
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('FONTSIZE', (0, 0), (-1, -1), 8.5),
        ('BACKGROUND', (0, 0), (-1, 0), SGS_RED),
        ('TEXTCOLOR', (0, 0), (-1, 0), white),
        ('GRID', (0, 0), (-1, -1), 0.5, lightgrey),
        ('ROWBACKGROUNDS', (0, 1), (-1, -1), [white, HexColor("#FFF5F5")]),
        ('ALIGN', (2, 0), (-1, -1), 'CENTER'),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 5),
        ('TOPPADDING', (0, 0), (-1, -1), 5),
    ]))
    story.append(t)
    story.append(Spacer(1, 16))

    story.append(Paragraph("<b>CONCLUSION</b>", heading_style))
    story.append(Paragraph(
        "The sample of lithium carbonate (Li<sub>2</sub>CO<sub>3</sub>) from Lot MA-LOT-2026-0047 "
        "<b>MEETS</b> all requirements of ISO 6206:2023 for battery-grade lithium carbonate with "
        "a measured purity of <b>99.71%</b>, well above the 99.0% minimum threshold. "
        "All tested parameters fall within the specified limits. The material is suitable for "
        "use in lithium-ion battery cathode (NMC 811) manufacturing.", body_style))
    story.append(Spacer(1, 8))
    story.append(Paragraph(
        "<i>Note: This certificate relates only to the items tested and does not imply approval "
        "of the entire lot. Results reported on a dry-weight basis unless otherwise stated.</i>",
        small_style))
    story.append(Spacer(1, 30))

    # Signature block
    sig = [
        ["", ""],
        ["_________________________", "_________________________"],
        ["Dr. María Soledad Vega Rojas", "Ing. Tomás Herrera Aravena"],
        ["Laboratory Director", "Quality Manager"],
        ["SGS Chile Ltda., Antofagasta", "SGS Chile Ltda., Antofagasta"],
    ]
    t = Table(sig, colWidths=[220, 220])
    t.setStyle(TableStyle([
        ('FONTNAME', (0, 0), (-1, -1), 'Helvetica'),
        ('FONTSIZE', (0, 0), (-1, -1), 9),
        ('FONTNAME', (0, 2), (-1, 2), 'Helvetica-Bold'),
        ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
        ('TOPPADDING', (0, 0), (-1, -1), 2),
    ]))
    story.append(t)

    doc.build(story, onFirstPage=first_page, onLaterPages=first_page)
    print(f"  ✅ {path}")


# ═══════════════════════════════════════════════════════════════════
# 2. SGS Pre-Shipment Inspection Report (Exporter Evidence)
# ═══════════════════════════════════════════════════════════════════

def gen_sgs_inspection():
    path = os.path.join(OUT, "02_SGS_PreShipment_Inspection.pdf")
    doc = SimpleDocTemplate(path, pagesize=A4,
                            topMargin=100, bottomMargin=60,
                            leftMargin=50, rightMargin=50)

    def first_page(c, d):
        colored_header_bar(c, d, SGS_RED, "SGS", "Inspection & Verification Services")
        footer(c, d, "SGS Chile Ltda. | Av. Andrés Bello 2233, Providencia, Santiago | RUT: 96.722.460-K")

    story = []
    story.append(Spacer(1, 30))
    story.append(Paragraph("<b>PRE-SHIPMENT INSPECTION REPORT</b>",
                           make_style('t', fontSize=14, alignment=TA_CENTER, textColor=SGS_RED)))
    story.append(Spacer(1, 4))
    story.append(Paragraph("Report No: CL-ANT-PSI-2026-01203 | Date: 2026-01-22",
                           make_style('t', fontSize=9, alignment=TA_CENTER, textColor=grey)))
    story.append(Spacer(1, 20))

    info = [
        ["Applicant:", "Minera Andina SRL, Potosí, Bolivia"],
        ["Buyer:", "Electroquímica del Perú S.A., Lima, Peru"],
        ["Commodity:", "Battery-grade lithium carbonate (Li₂CO₃)"],
        ["Quantity:", "50 metric tons (net) in 2,000 × 25 kg bags"],
        ["Lot Reference:", "MA-LOT-2026-0047"],
        ["Inspection Location:", "Port of Antofagasta, Terminal 2, Warehouse B-7"],
        ["Inspection Date:", "2026-01-22, 08:00–16:30 CLT"],
        ["Inspector:", "Rodrigo Fuentes Pizarro (SGS ID: RF-4521)"],
    ]
    t = Table(info, colWidths=[110, 380])
    t.setStyle(TableStyle([
        ('FONTNAME', (0, 0), (0, -1), 'Helvetica-Bold'),
        ('FONTSIZE', (0, 0), (-1, -1), 9),
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
    ]))
    story.append(t)
    story.append(Spacer(1, 16))

    story.append(Paragraph("<b>1. CONTAINER LOADING DETAILS</b>", heading_style))
    containers = [
        ["Container No.", "Seal No.", "Bags", "Net Weight (kg)", "Tare (kg)", "Gross (kg)"],
        ["COSCU-123456-7", "SGS-CL-880214", "1,000", "25,000", "2,200", "27,200"],
        ["COSCU-123457-5", "SGS-CL-880215", "500", "12,500", "2,200", "14,700"],
        ["COSCU-123458-3", "SGS-CL-880216", "300", "7,500", "2,200", "9,700"],
        ["COSCU-123459-1", "SGS-CL-880217", "200", "5,000", "2,200", "7,200"],
        ["TOTAL", "", "2,000", "50,000", "8,800", "58,800"],
    ]
    t = Table(containers, colWidths=[90, 85, 50, 80, 55, 60])
    t.setStyle(TableStyle([
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('FONTSIZE', (0, 0), (-1, -1), 8.5),
        ('BACKGROUND', (0, 0), (-1, 0), SGS_RED),
        ('TEXTCOLOR', (0, 0), (-1, 0), white),
        ('GRID', (0, 0), (-1, -1), 0.5, lightgrey),
        ('ALIGN', (2, 0), (-1, -1), 'CENTER'),
        ('FONTNAME', (0, -1), (-1, -1), 'Helvetica-Bold'),
        ('BACKGROUND', (0, -1), (-1, -1), HexColor("#FFF0F0")),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
        ('TOPPADDING', (0, 0), (-1, -1), 4),
    ]))
    story.append(t)
    story.append(Spacer(1, 12))

    story.append(Paragraph("<b>2. VISUAL INSPECTION</b>", heading_style))
    story.append(Paragraph(
        "All 2,000 bags inspected for integrity. Bags are triple-layered (PE inner liner, "
        "kraft paper middle, PP woven outer) with heat-sealed closures. <b>Zero defects found.</b> "
        "Product appears as fine white powder consistent with battery-grade Li<sub>2</sub>CO<sub>3</sub>. "
        "No discoloration, clumping, or foreign matter observed.", body_style))
    story.append(Spacer(1, 8))

    story.append(Paragraph("<b>3. CONTAINER CONDITION</b>", heading_style))
    story.append(Paragraph(
        "All four 20' dry containers inspected before loading. Containers are clean, dry, "
        "odor-free, and structurally sound. <b>No damage, holes, or moisture ingress points detected.</b> "
        "Desiccant strips (CaCl<sub>2</sub> type, 500g × 8 per container) installed per "
        "shipper's instructions. Container floors lined with PE sheeting.", body_style))
    story.append(Spacer(1, 8))

    story.append(Paragraph("<b>4. SEALING</b>", heading_style))
    story.append(Paragraph(
        "All containers sealed in the presence of the SGS inspector. "
        "SGS bolt seals applied (serial numbers SGS-CL-880214 through SGS-CL-880217). "
        "Photographic evidence recorded (SGS Photo Set CL-ANT-PSI-2026-01203-PH, 47 photos). "
        "GPS coordinates at sealing: -23.6345, -70.3958 (Port of Antofagasta).", body_style))
    story.append(Spacer(1, 8))

    story.append(Paragraph("<b>5. DOCUMENTATION VERIFIED</b>", heading_style))
    docs = [
        "• Certificate of Analysis CL-ANT-2026-04871 (SGS Chile, dated 2026-01-15)",
        "• ISO 6206:2023 compliance declaration by Minera Andina SRL",
        "• Material Safety Data Sheet (MSDS) — Li₂CO₃ battery grade",
        "• Commercial invoice INV-ANDINA-2026-0334",
        "• Packing list PL-ANDINA-2026-0334 (2,000 bags × 25 kg)",
    ]
    for d in docs:
        story.append(Paragraph(d, body_style))
    story.append(Spacer(1, 12))

    story.append(Paragraph("<b>6. CONCLUSION</b>", heading_style))
    story.append(Paragraph(
        "Based on visual inspection, weight verification, container condition assessment, "
        "and documentation review, the shipment of 50 metric tons of battery-grade lithium "
        "carbonate from Minera Andina SRL is <b>IN CONFORMITY</b> with the buyer's purchase "
        "order specifications and ready for shipment to Callao, Peru.", body_style))
    story.append(Spacer(1, 24))

    sig = [["_________________________"],
           ["Rodrigo Fuentes Pizarro"],
           ["Lead Inspector, SGS Chile"],
           ["License No. RF-4521"]]
    t = Table(sig, colWidths=[200])
    t.setStyle(TableStyle([
        ('FONTSIZE', (0, 0), (-1, -1), 9),
        ('FONTNAME', (0, 1), (0, 1), 'Helvetica-Bold'),
        ('TOPPADDING', (0, 0), (-1, -1), 2),
    ]))
    story.append(t)

    doc.build(story, onFirstPage=first_page, onLaterPages=first_page)
    print(f"  ✅ {path}")


# ═══════════════════════════════════════════════════════════════════
# 3. COSCO Bill of Lading (Neutral/Exporter Evidence)
# ═══════════════════════════════════════════════════════════════════

def gen_bill_of_lading():
    path = os.path.join(OUT, "03_COSCO_Bill_of_Lading.pdf")
    doc = SimpleDocTemplate(path, pagesize=A4,
                            topMargin=100, bottomMargin=60,
                            leftMargin=40, rightMargin=40)

    def first_page(c, d):
        w, h = d.pagesize
        # COSCO blue header
        c.saveState()
        c.setFillColor(COSCO_BLUE)
        c.rect(0, h - 80, w, 80, fill=1, stroke=0)
        c.setFillColor(white)
        c.setFont("Helvetica-Bold", 22)
        c.drawString(50, h - 42, "COSCO SHIPPING")
        c.setFont("Helvetica", 10)
        c.drawString(50, h - 60, "COSCO Shipping Lines Co., Ltd.")
        # BL Number
        c.setFont("Helvetica-Bold", 11)
        c.drawRightString(w - 50, h - 42, "BILL OF LADING")
        c.setFont("Courier-Bold", 10)
        c.drawRightString(w - 50, h - 58, "B/L No: COSU-BOL-2026-001847")
        c.restoreState()
        footer(c, d, "COSCO Shipping Lines Co., Ltd. | Original — 3/3 | Non-negotiable copy")

    story = []
    story.append(Spacer(1, 30))

    # BL fields
    bl_fields = [
        ["Shipper:", "Minera Andina SRL\nZona Industrial km 12, Potosí, Bolivia\nTax ID: 1029384756\nContact: Ing. Carlos Quispe Mamani"],
        ["Consignee:", "Electroquímica del Perú S.A.\nAv. Argentina 4051, Callao, Lima, Peru\nRUC: 20512345678\nContact: Lic. Fernando Rojas Mendoza"],
        ["Notify Party:", "Same as consignee"],
        ["Vessel / Voyage:", "MV COSCO ATACAMA / Voyage 2026-SA-012W"],
        ["Port of Loading:", "Antofagasta, Chile"],
        ["Port of Discharge:", "Callao, Peru"],
        ["Place of Delivery:", "Callao, Peru (CIF)"],
    ]
    t = Table(bl_fields, colWidths=[100, 400])
    t.setStyle(TableStyle([
        ('FONTNAME', (0, 0), (0, -1), 'Helvetica-Bold'),
        ('FONTSIZE', (0, 0), (-1, -1), 9),
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ('BOX', (0, 0), (-1, -1), 0.5, COSCO_BLUE),
        ('INNERGRID', (0, 0), (-1, -1), 0.25, lightgrey),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 6),
        ('TOPPADDING', (0, 0), (-1, -1), 4),
        ('LEFTPADDING', (0, 0), (-1, -1), 6),
    ]))
    story.append(t)
    story.append(Spacer(1, 12))

    story.append(Paragraph("<b>PARTICULARS DECLARED BY SHIPPER</b>",
                           make_style('t', fontSize=11, textColor=COSCO_BLUE)))
    story.append(Spacer(1, 6))

    cargo = [
        ["Container No.", "Seal No.", "Marks", "Description", "Gross Weight", "Measurement"],
        ["COSCU-123456-7", "SGS-CL-880214", "MA-2026\nLot 0047", "LITHIUM CARBONATE\n(Li2CO3) BATTERY GRADE\nISO 6206 CERTIFIED\n1,000 bags × 25 kg", "27,200 kg", "33.2 CBM"],
        ["COSCU-123457-5", "SGS-CL-880215", "MA-2026\nLot 0047", "LITHIUM CARBONATE\n(Li2CO3) BATTERY GRADE\n500 bags × 25 kg", "14,700 kg", "33.2 CBM"],
        ["COSCU-123458-3", "SGS-CL-880216", "MA-2026\nLot 0047", "LITHIUM CARBONATE\n(Li2CO3) BATTERY GRADE\n300 bags × 25 kg", "9,700 kg", "33.2 CBM"],
        ["COSCU-123459-1", "SGS-CL-880217", "MA-2026\nLot 0047", "LITHIUM CARBONATE\n(Li2CO3) BATTERY GRADE\n200 bags × 25 kg", "7,200 kg", "33.2 CBM"],
    ]
    t = Table(cargo, colWidths=[80, 72, 55, 145, 62, 62])
    t.setStyle(TableStyle([
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('FONTSIZE', (0, 0), (-1, -1), 7.5),
        ('BACKGROUND', (0, 0), (-1, 0), COSCO_BLUE),
        ('TEXTCOLOR', (0, 0), (-1, 0), white),
        ('GRID', (0, 0), (-1, -1), 0.5, COSCO_BLUE),
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
        ('TOPPADDING', (0, 0), (-1, -1), 3),
    ]))
    story.append(t)
    story.append(Spacer(1, 12))

    story.append(Paragraph(
        "<b>Total: 4 × 20' containers | 2,000 bags | 50,000 kg net | 58,800 kg gross</b>",
        make_style('t', fontSize=9, alignment=TA_CENTER)))
    story.append(Spacer(1, 8))

    story.append(Paragraph(
        "SHIPPED ON BOARD DATE: 2026-01-24 | FREIGHT: PREPAID | "
        "INCOTERMS: CIF Callao, Peru (Incoterms 2020)",
        make_style('t', fontSize=8.5, alignment=TA_CENTER, textColor=COSCO_BLUE)))
    story.append(Spacer(1, 20))

    story.append(Paragraph(
        "<i>The goods described above are received in apparent good order and condition "
        "(unless otherwise noted) for carriage from the port of loading to the port of "
        "discharge subject to the terms and conditions of this Bill of Lading.</i>",
        make_style('t', fontSize=8, leading=11)))
    story.append(Spacer(1, 4))
    story.append(Paragraph(
        "Issued at Antofagasta, Chile on 2026-01-24. THREE (3) original Bills of Lading.",
        make_style('t', fontSize=8.5)))
    story.append(Spacer(1, 30))

    sig = [["For and on behalf of the Carrier", ""],
           ["COSCO Shipping Lines Co., Ltd.", ""],
           ["", ""],
           ["_________________________", ""],
           ["Capt. Wei Zhang, Agent", ""]]
    t = Table(sig, colWidths=[250, 200])
    t.setStyle(TableStyle([
        ('FONTSIZE', (0, 0), (-1, -1), 9),
        ('FONTNAME', (0, 0), (0, 0), 'Helvetica-Bold'),
        ('TOPPADDING', (0, 0), (-1, -1), 2),
    ]))
    story.append(t)

    doc.build(story, onFirstPage=first_page, onLaterPages=first_page)
    print(f"  ✅ {path}")


# ═══════════════════════════════════════════════════════════════════
# 4. Bureau Veritas Lab Analysis (Importer Evidence)
# ═══════════════════════════════════════════════════════════════════

def gen_bv_analysis():
    path = os.path.join(OUT, "04_BureauVeritas_Lab_Analysis.pdf")
    doc = SimpleDocTemplate(path, pagesize=A4,
                            topMargin=100, bottomMargin=60,
                            leftMargin=50, rightMargin=50)

    def first_page(c, d):
        w, h = d.pagesize
        # BV style header — orange accent bar + dark blue
        c.saveState()
        c.setFillColor(BV_DARK)
        c.rect(0, h - 75, w, 75, fill=1, stroke=0)
        c.setFillColor(BV_ORANGE)
        c.rect(0, h - 80, w, 5, fill=1, stroke=0)
        c.setFillColor(white)
        c.setFont("Helvetica-Bold", 20)
        c.drawString(50, h - 42, "Bureau Veritas")
        c.setFont("Helvetica", 9)
        c.drawString(50, h - 58, "Commodities, Industry & Facilities Division — Peru")
        c.setFont("Helvetica-Bold", 9)
        c.drawRightString(w - 50, h - 42, "ANALYTICAL REPORT")
        c.setFont("Courier", 8)
        c.drawRightString(w - 50, h - 55, "Ref: BV-LIM-2026-AN-00412")
        c.restoreState()
        footer(c, d, "Bureau Veritas del Perú S.A. | Calle Mártir Olaya 129, Miraflores, Lima | RUC: 20100072524 | www.bureauveritas.com.pe")

    story = []
    story.append(Spacer(1, 30))
    story.append(Paragraph("<b>INDEPENDENT LABORATORY ANALYSIS REPORT</b>",
                           make_style('t', fontSize=13, alignment=TA_CENTER, textColor=BV_DARK)))
    story.append(Paragraph("Lithium Carbonate Quality Verification",
                           make_style('t', fontSize=10, alignment=TA_CENTER, textColor=BV_ORANGE)))
    story.append(Spacer(1, 16))

    info = [
        ["Requested by:", "Electroquímica del Perú S.A."],
        ["Contact:", "Lic. Fernando Rojas Mendoza, Head of Procurement"],
        ["Subject:", "Verification analysis of lithium carbonate shipment per P.O. EP-PO-2026-0178"],
        ["Origin:", "Minera Andina SRL, Bolivia (Lot MA-LOT-2026-0047)"],
        ["Sample collected:", "2026-02-08 at Port of Callao, Container COSCU-123456-7"],
        ["Sampling method:", "ASTM E300-03 (random stratified, 12 samples from 1,000 bags)"],
        ["Report date:", "2026-02-12"],
    ]
    t = Table(info, colWidths=[100, 390])
    t.setStyle(TableStyle([
        ('FONTNAME', (0, 0), (0, -1), 'Helvetica-Bold'),
        ('FONTSIZE', (0, 0), (-1, -1), 9),
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
    ]))
    story.append(t)
    story.append(Spacer(1, 14))

    story.append(Paragraph("<b>IMPORTANT NOTE ON CONTAINER CONDITION</b>",
                           make_style('t', fontSize=10, textColor=red)))
    story.append(Paragraph(
        "Upon arrival at Callao on 2026-02-06, containers COSCU-123458-3 and COSCU-123459-1 "
        "exhibited <b>compromised seal integrity</b>. Seal SGS-CL-880216 on COSCU-123458-3 "
        "showed signs of corrosion and partial fracture. Seal SGS-CL-880217 on COSCU-123459-1 "
        "was intact but the container door gasket was degraded, with visible moisture condensation "
        "on interior walls. Containers COSCU-123456-7 and COSCU-123457-5 were in good condition "
        "with intact seals.", body_style))
    story.append(Spacer(1, 12))

    story.append(Paragraph("<b>ANALYTICAL RESULTS</b>", heading_style))
    story.append(Paragraph(
        "Method: ICP-MS (Inductively Coupled Plasma Mass Spectrometry), Agilent 7900. "
        "BV Lima laboratory accreditation: INACAL-DA No. LP-042-2024.",
        make_style('t', fontSize=8, textColor=grey)))
    story.append(Spacer(1, 6))

    results = [
        ["Parameter", "Method", "Containers\n1 & 2", "Containers\n3 & 4", "ISO 6206\nBG Spec", "Verdict"],
        ["Li₂CO₃ purity", "ICP-MS", "99.02%", "98.22%", "≥ 99.0%", "FAIL"],
        ["Na content", "ICP-MS", "0.028%", "0.051%", "≤ 0.04%", "MARGINAL"],
        ["Ca content", "ICP-MS", "0.013%", "0.029%", "≤ 0.02%", "FAIL*"],
        ["Mg content", "ICP-MS", "0.006%", "0.013%", "≤ 0.01%", "FAIL*"],
        ["Fe content", "ICP-MS", "11 ppm", "24 ppm", "≤ 20 ppm", "FAIL*"],
        ["Moisture (H₂O)", "Karl Fischer", "0.19%", "0.78%", "≤ 0.30%", "FAIL*"],
        ["Cl content", "IC", "0.004%", "0.009%", "≤ 0.01%", "PASS"],
    ]

    t = Table(results, colWidths=[80, 55, 68, 68, 68, 60])
    t.setStyle(TableStyle([
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('FONTSIZE', (0, 0), (-1, -1), 8),
        ('BACKGROUND', (0, 0), (-1, 0), BV_DARK),
        ('TEXTCOLOR', (0, 0), (-1, 0), white),
        ('GRID', (0, 0), (-1, -1), 0.5, lightgrey),
        ('ALIGN', (2, 0), (-1, -1), 'CENTER'),
        ('TEXTCOLOR', (5, 1), (5, 1), red),
        ('FONTNAME', (5, 1), (5, 1), 'Helvetica-Bold'),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
        ('TOPPADDING', (0, 0), (-1, -1), 4),
    ]))
    story.append(t)
    story.append(Spacer(1, 4))
    story.append(Paragraph(
        "<i>* Failures marked with asterisk apply only to containers 3 &amp; 4 (moisture-affected). "
        "Containers 1 &amp; 2 are marginal but below spec on primary purity metric.</i>",
        small_style))
    story.append(Spacer(1, 12))

    story.append(Paragraph("<b>WEIGHTED AVERAGE PURITY (ALL 4 CONTAINERS)</b>", heading_style))
    story.append(Paragraph(
        "Weighted by container volume: (25,000 × 99.02% + 12,500 × 99.02% + 7,500 × 98.22% "
        "+ 5,000 × 98.22%) / 50,000 = <b>98.82%</b>. Overall purity falls below the ISO 6206 "
        "battery-grade minimum of 99.0% by 0.18 percentage points. Containers 1 and 2 are "
        "marginally above spec (99.02%) while containers 3 and 4 are clearly below (98.22%). "
        "Notably, even the SGS pre-shipment certificate reports a substantially higher purity "
        "of 99.71%, creating an irreconcilable discrepancy of 0.89 percentage points between "
        "origin and destination measurements.", body_style))
    story.append(Spacer(1, 8))

    # Use a simplified "Blended" figure
    story.append(Paragraph("<b>CONCLUSION</b>", heading_style))
    story.append(Paragraph(
        "The shipment of lithium carbonate from Lot MA-LOT-2026-0047 <b>DOES NOT MEET</b> "
        "the requirements of ISO 6206:2023 for battery-grade material on a blended basis. "
        "The weighted average purity of <b>98.82%</b> is below the 99.0% minimum threshold. "
        "There is a notable discrepancy between the SGS origin certificate (99.71%) and our "
        "destination measurements: containers 1 &amp; 2 show 99.02% (marginally above spec but "
        "far below the claimed 99.71%), while containers 3 &amp; 4 show 98.22% (clearly below spec). "
        "The degradation in containers 3 &amp; 4 is consistent with moisture ingress from the "
        "compromised seal on COSCU-123458-3 and degraded gasket on COSCU-123459-1. "
        "The unexplained discrepancy in containers 1 &amp; 2 (99.02% vs 99.71% claimed) may reflect "
        "sampling variability, instrument calibration differences between ICP-OES and ICP-MS, "
        "or minor in-transit degradation. Bureau Veritas makes no determination on causation "
        "but notes that all four containers fail to deliver the specification claimed by the seller.",
        body_style))
    story.append(Spacer(1, 8))

    story.append(Paragraph("<b>RECOMMENDATION</b>", heading_style))
    story.append(Paragraph(
        "Bureau Veritas recommends the buyer request renegotiation or partial credit. "
        "The material in containers 3 and 4 (12,500 kg) at 98.22% is clearly unsuitable for "
        "NMC 811 battery cathode use. The material in containers 1 and 2 (37,500 kg) at 99.02% "
        "is borderline — marginally above the ISO 6206 limit but significantly below the certified "
        "99.71% purity. Depending on the buyer's specific battery grade requirements, this material "
        "may or may not be acceptable. We recommend the buyer and seller engage technically on the "
        "cause of the origin-destination discrepancy before final disposition.",
        body_style))
    story.append(Spacer(1, 24))

    sig = [["_________________________", "_________________________"],
           ["Dra. Ana Lucía Paredes Vásquez", "Ing. Jorge Castillo Huertas"],
           ["Head of Minerals Laboratory", "Quality Assurance Manager"],
           ["Bureau Veritas del Perú S.A.", "Bureau Veritas del Perú S.A."]]
    t = Table(sig, colWidths=[220, 220])
    t.setStyle(TableStyle([
        ('FONTSIZE', (0, 0), (-1, -1), 9),
        ('FONTNAME', (0, 1), (-1, 1), 'Helvetica-Bold'),
        ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
        ('TOPPADDING', (0, 0), (-1, -1), 2),
    ]))
    story.append(t)

    doc.build(story, onFirstPage=first_page, onLaterPages=first_page)
    print(f"  ✅ {path}")


# ═══════════════════════════════════════════════════════════════════
# 5. Arrival Inspection Report (Importer Evidence)
# ═══════════════════════════════════════════════════════════════════

def gen_arrival_inspection():
    path = os.path.join(OUT, "05_Arrival_Inspection_Report.pdf")
    doc = SimpleDocTemplate(path, pagesize=A4,
                            topMargin=100, bottomMargin=60,
                            leftMargin=50, rightMargin=50)

    def first_page(c, d):
        w, h = d.pagesize
        # Electroquímica header — dark navy with gold accent
        c.saveState()
        c.setFillColor(ELECTRO_NAVY)
        c.rect(0, h - 75, w, 75, fill=1, stroke=0)
        c.setFillColor(ELECTRO_GOLD)
        c.rect(0, h - 78, w, 3, fill=1, stroke=0)
        c.setFillColor(white)
        c.setFont("Helvetica-Bold", 16)
        c.drawString(50, h - 38, u"ELECTROQUÍMICA DEL PERÚ S.A.")
        c.setFont("Helvetica", 9)
        c.setFillColor(ELECTRO_GOLD)
        c.drawString(50, h - 54, "Quality Control Department — Incoming Materials Division")
        c.restoreState()
        footer(c, d, u"Electroquímica del Perú S.A. | Av. Argentina 4051, Callao | RUC: 20512345678")

    story = []
    story.append(Spacer(1, 30))
    story.append(Paragraph("<b>INCOMING MATERIAL INSPECTION REPORT</b>",
                           make_style('t', fontSize=13, alignment=TA_CENTER, textColor=ELECTRO_NAVY)))
    story.append(Paragraph("Report No: EP-QC-INS-2026-0089 | Date: 2026-02-08",
                           make_style('t', fontSize=9, alignment=TA_CENTER, textColor=grey)))
    story.append(Spacer(1, 16))

    story.append(Paragraph("<b>SHIPMENT DETAILS</b>", heading_style))
    info = [
        ["Supplier:", "Minera Andina SRL, Potosí, Bolivia"],
        ["P.O. Reference:", "EP-PO-2026-0178"],
        ["B/L Number:", "COSU-BOL-2026-001847"],
        ["Vessel:", "MV COSCO ATACAMA / Voyage 2026-SA-012W"],
        ["ETA / Actual arrival:", "2026-02-04 / 2026-02-06 (2 days late)"],
        ["Inspector:", "Ing. Patricia Velarde Ríos (EP-QC-027)"],
    ]
    t = Table(info, colWidths=[110, 380])
    t.setStyle(TableStyle([
        ('FONTNAME', (0, 0), (0, -1), 'Helvetica-Bold'),
        ('FONTSIZE', (0, 0), (-1, -1), 9),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 3),
    ]))
    story.append(t)
    story.append(Spacer(1, 12))

    story.append(Paragraph("<b>CONTAINER CONDITION ON ARRIVAL</b>", heading_style))

    cond = [
        ["Container", "Seal Status", "Door Gasket", "Interior", "Overall"],
        ["COSCU-123456-7", "INTACT ✓", "Good", "Dry, clean", "ACCEPTABLE"],
        ["COSCU-123457-5", "INTACT ✓", "Good", "Dry, clean", "ACCEPTABLE"],
        ["COSCU-123458-3", "CORRODED ✗", "Degraded", "Moisture on walls\nCondensation visible", "REJECTED"],
        ["COSCU-123459-1", "INTACT ✓", "Degraded", "Moisture on walls\n~15 bags damp", "REJECTED"],
    ]
    t = Table(cond, colWidths=[82, 70, 65, 105, 75])
    t.setStyle(TableStyle([
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('FONTSIZE', (0, 0), (-1, -1), 8),
        ('BACKGROUND', (0, 0), (-1, 0), ELECTRO_NAVY),
        ('TEXTCOLOR', (0, 0), (-1, 0), white),
        ('GRID', (0, 0), (-1, -1), 0.5, lightgrey),
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ('TEXTCOLOR', (4, 3), (4, 4), red),
        ('FONTNAME', (4, 3), (4, 4), 'Helvetica-Bold'),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
        ('TOPPADDING', (0, 0), (-1, -1), 3),
    ]))
    story.append(t)
    story.append(Spacer(1, 10))

    story.append(Paragraph("<b>PHOTOGRAPHIC EVIDENCE</b>", heading_style))
    story.append(Paragraph(
        "62 photographs taken and filed under EP-QC-PHOTO-2026-0089. Key findings:", body_style))
    photos = [
        "• Photos 01–08: Container exteriors showing shipping line markings and condition",
        "• Photos 09–14: Seal close-ups — SGS-CL-880216 shows visible corrosion and fracture line",
        "• Photos 15–22: Interior of containers 3 &amp; 4 — condensation droplets on ceiling and walls",
        "• Photos 23–30: Damp bags in container 4 — approximately 15 bags with visible moisture staining",
        "• Photos 31–40: Product samples taken from each container for lab analysis",
        "• Photos 41–62: General condition, labeling, and bag integrity",
    ]
    for p in photos:
        story.append(Paragraph(p, make_style('t', fontSize=9, leading=12)))
    story.append(Spacer(1, 10))

    story.append(Paragraph("<b>INITIAL ASSESSMENT</b>", heading_style))
    story.append(Paragraph(
        "Two of four containers show evidence of moisture exposure during transit. "
        "The compromised seal on COSCU-123458-3 and degraded gasket on COSCU-123459-1 "
        "likely allowed salt-laden marine air to contact the lithium carbonate, which is "
        "hygroscopic. Samples from all four containers have been sent to Bureau Veritas Lima "
        "for independent purity analysis (BV Ref: BV-LIM-2026-AN-00412).", body_style))
    story.append(Spacer(1, 6))
    story.append(Paragraph(
        "<b>Recommendation:</b> Hold entire shipment pending lab results. Do not release "
        "to production. Notify supplier of non-conformity on containers 3 and 4.",
        make_style('t', fontSize=10, textColor=red)))

    story.append(Spacer(1, 24))
    sig = [["_________________________"],
           ["Ing. Patricia Velarde Ríos"],
           ["QC Inspector, EP-QC-027"],
           [u"Electroquímica del Perú S.A."]]
    t = Table(sig, colWidths=[200])
    t.setStyle(TableStyle([
        ('FONTSIZE', (0, 0), (-1, -1), 9),
        ('FONTNAME', (0, 1), (0, 1), 'Helvetica-Bold'),
        ('TOPPADDING', (0, 0), (-1, -1), 2),
    ]))
    story.append(t)

    doc.build(story, onFirstPage=first_page, onLaterPages=first_page)
    print(f"  ✅ {path}")


# ═══════════════════════════════════════════════════════════════════
# 6. Formal Rejection Notice (Importer Evidence)
# ═══════════════════════════════════════════════════════════════════

def gen_rejection_notice():
    path = os.path.join(OUT, "06_Formal_Rejection_Notice.pdf")
    doc = SimpleDocTemplate(path, pagesize=letter,
                            topMargin=90, bottomMargin=60,
                            leftMargin=60, rightMargin=60)

    def first_page(c, d):
        w, h = d.pagesize
        # Formal letterhead — minimal, professional
        c.saveState()
        c.setFillColor(ELECTRO_NAVY)
        c.rect(55, h - 30, w - 110, 2, fill=1, stroke=0)
        c.setFont("Helvetica-Bold", 13)
        c.drawString(60, h - 52, u"ELECTROQUÍMICA DEL PERÚ S.A.")
        c.setFont("Helvetica", 8)
        c.setFillColor(grey)
        c.drawString(60, h - 64, "Av. Argentina 4051, Callao, Lima | RUC: 20512345678 | Tel: +51 1 452 7800")
        c.drawString(60, h - 74, "www.electroquimicaperu.com.pe | procurement@electroquimicaperu.com.pe")
        c.restoreState()
        footer(c, d, "Sent via registered courier (Olva Courier) and email")

    story = []
    story.append(Spacer(1, 20))

    # Date and reference
    story.append(Paragraph("Lima, February 14, 2026", right_style))
    story.append(Spacer(1, 12))
    story.append(Paragraph("<b>Ref: FORMAL NOTICE OF NON-CONFORMITY AND REJECTION</b>", body_style))
    story.append(Paragraph("P.O. No: EP-PO-2026-0178 | B/L: COSU-BOL-2026-001847", body_style))
    story.append(Spacer(1, 12))

    # Addressee
    story.append(Paragraph("To:", make_style('t', fontSize=10)))
    story.append(Paragraph("<b>Minera Andina SRL</b>", body_style))
    story.append(Paragraph("Attn: Ing. Carlos Quispe Mamani, Export Manager", body_style))
    story.append(Paragraph("Zona Industrial km 12, Potosí, Bolivia", body_style))
    story.append(Paragraph("Email: c.quispe@minerandina.bo", body_style))
    story.append(Spacer(1, 16))

    story.append(Paragraph("Dear Ing. Quispe Mamani,", body_style))
    story.append(Spacer(1, 8))

    story.append(Paragraph(
        "We write to formally notify you that the shipment of 50 metric tons of battery-grade "
        "lithium carbonate (Li<sub>2</sub>CO<sub>3</sub>) received at the Port of Callao on "
        "February 6, 2026 under Bill of Lading COSU-BOL-2026-001847 <b>does not conform</b> "
        "to the specifications agreed in Purchase Order EP-PO-2026-0178 and referenced in your "
        "Certificate of Analysis CL-ANT-2026-04871.", body_style))
    story.append(Spacer(1, 8))

    story.append(Paragraph("<b>Grounds for Rejection:</b>", body_style))
    story.append(Spacer(1, 4))

    grounds = [
        "<b>1. Purity Below Specification.</b> Independent analysis by Bureau Veritas del Perú "
        "(Report BV-LIM-2026-AN-00412, dated February 12, 2026) determined a weighted average "
        "Li<sub>2</sub>CO<sub>3</sub> purity of <b>98.54%</b>, which is below the ISO 6206 "
        "battery-grade minimum of 99.0% and the contractually agreed specification.",

        "<b>2. Container Integrity Failure.</b> Two of four containers (COSCU-123458-3 and "
        "COSCU-123459-1) arrived with compromised seals and/or degraded door gaskets, resulting "
        "in moisture ingress. Material in these containers showed purity of only 97.54% and "
        "moisture content of 1.47% (specification: ≤ 0.30%).",

        "<b>3. Material Unsuitable for Intended Use.</b> The purchased material was specified "
        "for lithium-ion battery cathode precursor manufacturing (NMC 811). Material below "
        "99.0% purity introduces unacceptable impurity levels that compromise cell performance "
        "and safety. We cannot use this material in our production process.",
    ]
    for g in grounds:
        story.append(Paragraph(g, body_style))
        story.append(Spacer(1, 6))

    story.append(Spacer(1, 4))
    story.append(Paragraph("<b>Remedy Requested:</b>", body_style))
    story.append(Spacer(1, 4))
    story.append(Paragraph(
        "In accordance with Article 12.3 of our Purchase Agreement and CISG Article 46(2), "
        "we request:", body_style))
    story.append(Spacer(1, 4))

    remedies = [
        "(a) Full replacement of the 50 MT shipment with material meeting ISO 6206 battery-grade "
        "specifications, delivered CIF Callao within 45 days; <b>OR</b>",
        "(b) A price reduction of 35% reflecting the downgrade from battery-grade to "
        "technical-grade material, plus compensation for additional costs incurred (storage at "
        "Callao: USD 4,200; re-testing: USD 1,800; production delay penalties: USD 28,000).",
    ]
    for r in remedies:
        story.append(Paragraph(r, body_style))
        story.append(Spacer(1, 4))

    story.append(Spacer(1, 6))
    story.append(Paragraph(
        "Please respond within <b>fifteen (15) business days</b> of receipt of this notice. "
        "Failure to respond or reach an agreement will result in our initiating dispute resolution "
        "proceedings per Article 18 of the Purchase Agreement.", body_style))
    story.append(Spacer(1, 8))
    story.append(Paragraph(
        "The shipment remains held at Callao bonded warehouse (RANSA, Bay 14) at your risk "
        "and expense pending resolution.", body_style))
    story.append(Spacer(1, 16))

    story.append(Paragraph("Respectfully,", body_style))
    story.append(Spacer(1, 24))
    story.append(Paragraph("_________________________", body_style))
    story.append(Paragraph("<b>Lic. Fernando Rojas Mendoza</b>", body_style))
    story.append(Paragraph("Head of Procurement", body_style))
    story.append(Paragraph(u"Electroquímica del Perú S.A.", body_style))
    story.append(Spacer(1, 12))
    story.append(Paragraph("<i>CC: Legal Department (EP), COSCO Shipping (claims), "
                           "Insurance — Rímac Seguros (Policy COM-2026-44891)</i>", small_style))

    doc.build(story, onFirstPage=first_page, onLaterPages=first_page)
    print(f"  ✅ {path}")


# ═══════════════════════════════════════════════════════════════════
# 7. Purchase Contract Excerpt (Neutral)
# ═══════════════════════════════════════════════════════════════════

def gen_contract_excerpt():
    path = os.path.join(OUT, "07_Purchase_Contract_Excerpt.pdf")
    doc = SimpleDocTemplate(path, pagesize=letter,
                            topMargin=90, bottomMargin=60,
                            leftMargin=65, rightMargin=65)

    def first_page(c, d):
        w, h = d.pagesize
        c.saveState()
        # Formal legal document style
        c.setStrokeColor(ICC_BLUE)
        c.setLineWidth(2)
        c.line(60, h - 35, w - 60, h - 35)
        c.line(60, h - 38, w - 60, h - 38)
        c.setFont("Helvetica-Bold", 12)
        c.setFillColor(ICC_BLUE)
        c.drawCentredString(w / 2, h - 58, "INTERNATIONAL SALE AND PURCHASE AGREEMENT")
        c.setFont("Helvetica", 9)
        c.drawCentredString(w / 2, h - 72, "Contract No: ISPA-2025-BOL-PER-0047 | Executed: November 28, 2025")
        c.restoreState()
        footer(c, d, "EXCERPT — Selected articles relevant to quality dispute | Full contract: 34 pages, 22 articles")

    story = []
    story.append(Spacer(1, 30))
    story.append(Paragraph("<b>PARTIES</b>", make_style('t', fontSize=11, textColor=ICC_BLUE)))
    story.append(Spacer(1, 6))
    story.append(Paragraph(
        '<b>"Seller":</b> Minera Andina SRL, a company organized under the laws of the '
        "Plurinational State of Bolivia, with registered offices at Zona Industrial km 12, "
        "Potosí, Bolivia (Tax ID: 1029384756).", body_style))
    story.append(Spacer(1, 4))
    story.append(Paragraph(
        '<b>"Buyer":</b> Electroquímica del Perú S.A., a company organized under the laws of '
        "the Republic of Peru, with registered offices at Av. Argentina 4051, Callao, Lima, "
        "Peru (RUC: 20512345678).", body_style))
    story.append(Spacer(1, 12))

    story.append(HRFlowable(width="100%", thickness=0.5, color=lightgrey))
    story.append(Spacer(1, 8))

    # Article 3 — Goods
    story.append(Paragraph("<b>ARTICLE 3 — GOODS AND SPECIFICATIONS</b>",
                           make_style('t', fontSize=10, textColor=ICC_BLUE)))
    story.append(Spacer(1, 4))
    story.append(Paragraph(
        "3.1 The Seller shall deliver to the Buyer <b>fifty (50) metric tons</b> of "
        "battery-grade lithium carbonate (Li<sub>2</sub>CO<sub>3</sub>), conforming to the "
        "following specifications:", body_style))
    story.append(Spacer(1, 4))

    specs = [
        ["Parameter", "Requirement"],
        ["Li₂CO₃ purity", "≥ 99.0% (per ISO 6206:2023, battery grade)"],
        ["Moisture (H₂O)", "≤ 0.30%"],
        ["Na content", "≤ 0.04%"],
        ["Fe content", "≤ 20 ppm"],
        ["Particle size D50", "100–200 μm"],
        ["Packaging", "25 kg bags, triple-layered (PE/kraft/PP)"],
    ]
    t = Table(specs, colWidths=[110, 340])
    t.setStyle(TableStyle([
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('FONTSIZE', (0, 0), (-1, -1), 9),
        ('BACKGROUND', (0, 0), (-1, 0), ICC_BLUE),
        ('TEXTCOLOR', (0, 0), (-1, 0), white),
        ('GRID', (0, 0), (-1, -1), 0.5, lightgrey),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
    ]))
    story.append(t)
    story.append(Spacer(1, 6))
    story.append(Paragraph(
        "3.2 The Seller shall provide a Certificate of Analysis from an internationally "
        "accredited laboratory (ISO/IEC 17025) for each lot shipped. The CoA shall be issued "
        "no more than 30 days prior to the date of shipment.", body_style))
    story.append(Spacer(1, 10))

    # Article 5 — Price
    story.append(Paragraph("<b>ARTICLE 5 — PRICE AND PAYMENT</b>",
                           make_style('t', fontSize=10, textColor=ICC_BLUE)))
    story.append(Spacer(1, 4))
    story.append(Paragraph(
        "5.1 The contract price is <b>BOB 500,000</b> (five hundred thousand Bolivian "
        "Bolivianos), equivalent to approximately PEN 247,400 at the reference exchange rate "
        "of <b>0.4948 PEN/BOB</b> (Banco Central de Reserva del Perú mid-rate, March 2026).", body_style))
    story.append(Paragraph(
        "5.2 Payment shall be made in Peruvian Soles (PEN) at the spot exchange rate "
        "published by the Banco Central de Reserva del Perú on the date of settlement. "
        "The settlement amount is calculated as: Invoice (BOB) × spot rate (PEN/BOB). "
        "If the actual rate deviates from the reference rate by more than <b>100 basis points "
        "(1%)</b>, either party may request renegotiation of the settlement amount. "
        "In production deployments, the rate would be locked at deal creation via a price oracle "
        "(e.g., Chainlink or Pyth Network).", body_style))
    story.append(Paragraph(
        "5.3 Payment terms: escrow funding within 10 business days of contract execution; "
        "final settlement within 5 business days of delivery confirmation.", body_style))
    story.append(Spacer(1, 10))

    # Article 7 — Delivery
    story.append(Paragraph("<b>ARTICLE 7 — DELIVERY AND RISK</b>",
                           make_style('t', fontSize=10, textColor=ICC_BLUE)))
    story.append(Spacer(1, 4))
    story.append(Paragraph(
        "7.1 Delivery terms: <b>CIF Callao, Peru (Incoterms 2020)</b>.", body_style))
    story.append(Paragraph(
        "7.2 The Seller shall arrange for pre-shipment inspection by an internationally "
        "recognized inspection company (SGS, Bureau Veritas, Intertek, or equivalent).", body_style))
    story.append(Paragraph(
        "7.3 Risk passes to the Buyer when the goods are loaded on board the vessel at the "
        "port of loading (Antofagasta, Chile).", body_style))
    story.append(Spacer(1, 10))

    # Article 12 — Quality
    story.append(Paragraph("<b>ARTICLE 12 — QUALITY CLAIMS AND INSPECTION</b>",
                           make_style('t', fontSize=10, textColor=ICC_BLUE)))
    story.append(Spacer(1, 4))
    story.append(Paragraph(
        "12.1 The Buyer shall inspect the goods within <b>ten (10) business days</b> of arrival "
        "at the port of discharge and notify the Seller in writing of any non-conformity within "
        "<b>fifteen (15) business days</b> of arrival.", body_style))
    story.append(Paragraph(
        "12.2 If the Buyer claims the goods do not conform to the specifications in Article 3, "
        "the Buyer shall commission an independent analysis by a laboratory accredited under "
        "ISO/IEC 17025, at the Buyer's initial expense.", body_style))
    story.append(Paragraph(
        "12.3 If the independent analysis confirms non-conformity, the Buyer may at its option: "
        "(a) reject the goods and demand replacement delivery within 45 days; "
        "(b) accept the goods at a reduced price reflecting the actual quality; or "
        "(c) negotiate an alternative remedy with the Seller.", body_style))
    story.append(Paragraph(
        "12.4 In case of rejection, the Seller bears all costs of return shipment, storage, "
        "and replacement. In case of price reduction, the Buyer also recovers the cost of the "
        "independent analysis.", body_style))
    story.append(Spacer(1, 10))

    # Article 18 — Disputes
    story.append(Paragraph("<b>ARTICLE 18 — DISPUTE RESOLUTION</b>",
                           make_style('t', fontSize=10, textColor=ICC_BLUE)))
    story.append(Spacer(1, 4))
    story.append(Paragraph(
        "18.1 The parties shall attempt to resolve any dispute arising out of or in connection "
        "with this Agreement through good-faith negotiations for a period of thirty (30) days.", body_style))
    story.append(Paragraph(
        "18.2 If the dispute is not resolved through negotiation, either party may submit the "
        "dispute to binding arbitration under the rules of the International Chamber of Commerce "
        "(ICC). The seat of arbitration shall be Lima, Peru. The language of arbitration shall be "
        "Spanish. The arbitral tribunal shall consist of one (1) arbitrator.", body_style))
    story.append(Paragraph(
        "18.3 The parties agree that, as a preliminary step before ICC arbitration, the dispute "
        "may be submitted to <b>GenLayer InternetCourt</b> for AI-assisted evaluation, where "
        "each party presents evidence in structured digital format. The InternetCourt verdict "
        "shall be non-binding but may be cited as evidence in subsequent ICC proceedings.", body_style))
    story.append(Paragraph(
        "18.4 This Agreement shall be governed by the United Nations Convention on Contracts "
        "for the International Sale of Goods (CISG) and, where CISG does not apply, by the "
        "laws of Peru.", body_style))
    story.append(Spacer(1, 20))

    story.append(HRFlowable(width="100%", thickness=0.5, color=lightgrey))
    story.append(Spacer(1, 8))
    story.append(Paragraph(
        "<i>This excerpt contains Articles 3, 5, 7, 12, and 18 of the full International Sale "
        "and Purchase Agreement ISPA-2025-BOL-PER-0047. The full agreement comprises 22 articles "
        "over 34 pages. Both parties have executed the full agreement on November 28, 2025.</i>",
        small_style))

    sig_data = [
        ["For the Seller:", "For the Buyer:"],
        ["", ""],
        ["_________________________", "_________________________"],
        ["Ing. Carlos Quispe Mamani", "Lic. Fernando Rojas Mendoza"],
        ["Export Manager", "Head of Procurement"],
        ["Minera Andina SRL", u"Electroquímica del Perú S.A."],
    ]
    t = Table(sig_data, colWidths=[210, 210])
    t.setStyle(TableStyle([
        ('FONTSIZE', (0, 0), (-1, -1), 9),
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('FONTNAME', (0, 3), (-1, 3), 'Helvetica-Bold'),
        ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
        ('TOPPADDING', (0, 0), (-1, -1), 2),
    ]))
    story.append(Spacer(1, 16))
    story.append(t)

    doc.build(story, onFirstPage=first_page, onLaterPages=first_page)
    print(f"  ✅ {path}")


# ═══════════════════════════════════════════════════════════════════
# 8. Bureau Veritas Lab Analysis — Case #19 (Clear Importer Win)
#    94.2% purity — three parameters fail — unambiguous non-conformity
# ═══════════════════════════════════════════════════════════════════

def gen_bv_analysis_case19():
    path = os.path.join(OUT, "08_BureauVeritas_Lab_Analysis_Case19.pdf")
    doc = SimpleDocTemplate(path, pagesize=A4,
                            topMargin=100, bottomMargin=60,
                            leftMargin=50, rightMargin=50)

    def first_page(c, d):
        w, h = d.pagesize
        c.saveState()
        c.setFillColor(BV_DARK)
        c.rect(0, h - 75, w, 75, fill=1, stroke=0)
        c.setFillColor(BV_ORANGE)
        c.rect(0, h - 80, w, 5, fill=1, stroke=0)
        c.setFillColor(white)
        c.setFont("Helvetica-Bold", 20)
        c.drawString(50, h - 42, "Bureau Veritas")
        c.setFont("Helvetica", 9)
        c.drawString(50, h - 58, "Commodities, Industry & Facilities Division — Peru")
        c.setFont("Helvetica-Bold", 9)
        c.drawRightString(w - 50, h - 42, "ANALYTICAL REPORT")
        c.setFont("Courier", 8)
        c.drawRightString(w - 50, h - 55, "Ref: BV-LIM-2026-AN-00618")
        c.restoreState()
        footer(c, d, "Bureau Veritas del Perú S.A. | Calle Mártir Olaya 129, Miraflores, Lima | RUC: 20100072524 | www.bureauveritas.com.pe")

    story = []
    story.append(Spacer(1, 30))
    story.append(Paragraph("<b>INDEPENDENT LABORATORY ANALYSIS REPORT</b>",
                           make_style('t', fontSize=13, alignment=TA_CENTER, textColor=BV_DARK)))
    story.append(Paragraph("Lithium Carbonate Quality Verification — Case #19 (PO EP-PO-2026-0219)",
                           make_style('t', fontSize=10, alignment=TA_CENTER, textColor=BV_ORANGE)))
    story.append(Spacer(1, 16))

    info = [
        ["Requested by:", "Electroquímica del Perú S.A."],
        ["Contact:", "Lic. Fernando Rojas Mendoza, Head of Procurement"],
        ["Subject:", "Verification analysis of lithium carbonate shipment per P.O. EP-PO-2026-0219"],
        ["Origin:", "Minera Andina SRL, Bolivia (Lot MA-LOT-2026-0119)"],
        ["B/L Reference:", "COSU-BOL-2026-003311 (MV COSCO PACIFIC, sailed 2026-02-18)"],
        ["Sample collected:", "2026-03-12 at Port of Callao, all 4 containers (composite)"],
        ["Sampling method:", "ASTM E300-03 (random stratified, 20 samples across all containers)"],
        ["Report date:", "2026-03-15"],
    ]
    t = Table(info, colWidths=[100, 390])
    t.setStyle(TableStyle([
        ('FONTNAME', (0, 0), (0, -1), 'Helvetica-Bold'),
        ('FONTSIZE', (0, 0), (-1, -1), 9),
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
    ]))
    story.append(t)
    story.append(Spacer(1, 14))

    story.append(Paragraph("<b>EXECUTIVE SUMMARY</b>",
                           make_style('t', fontSize=10, textColor=BV_DARK)))
    story.append(Paragraph(
        "This shipment shows <b>severe non-conformity</b> across three independent parameters: "
        "purity (94.2% vs 99.5% specification), moisture (0.41% vs ≤ 0.2%), and particle size "
        "(D90 52 μm vs ≤ 30 μm). The magnitude of deviation is consistent with use of an inferior "
        "commercial-grade feedstock rather than battery-grade Li<sub>2</sub>CO<sub>3</sub>, and "
        "is <b>irreconcilable</b> with the SGS pre-shipment certificate claiming 99.71% purity.",
        make_style('t', fontSize=9.5, leading=13, textColor=red)))
    story.append(Spacer(1, 14))

    story.append(Paragraph("<b>ANALYTICAL RESULTS</b>", heading_style))
    story.append(Paragraph(
        "Method: ICP-MS (Inductively Coupled Plasma Mass Spectrometry), Agilent 7900. "
        "Particle size: Malvern Mastersizer 3000 (laser diffraction, dry dispersion). "
        "Moisture: Karl Fischer coulometric titration. "
        "BV Lima laboratory accreditation: INACAL-DA No. LP-042-2024 (ISO/IEC 17025:2017).",
        make_style('t', fontSize=8, textColor=grey)))
    story.append(Spacer(1, 6))

    results = [
        ["Parameter", "Method", "Measured\nResult", "Contract\nSpecification", "ISO 6206\nBG Spec", "Status"],
        ["Li₂CO₃ purity", "ICP-MS", "94.2%", "≥ 99.5%", "≥ 99.0%", "FAIL"],
        ["Moisture (H₂O)", "Karl Fischer", "0.41%", "≤ 0.2%", "≤ 0.30%", "FAIL"],
        ["Particle size D90", "Laser diffraction", "52 μm", "≤ 30 μm", "Not spec'd", "FAIL"],
        ["Na content", "ICP-MS", "0.084%", "≤ 0.04%", "≤ 0.04%", "FAIL"],
        ["Ca content", "ICP-MS", "0.038%", "≤ 0.02%", "≤ 0.02%", "FAIL"],
        ["Mg content", "ICP-MS", "0.022%", "≤ 0.01%", "≤ 0.01%", "FAIL"],
        ["Fe content", "ICP-MS", "67 ppm", "≤ 20 ppm", "≤ 20 ppm", "FAIL"],
        ["SO₄ content", "IC", "0.18%", "≤ 0.05%", "≤ 0.05%", "FAIL"],
        ["Cl content", "IC", "0.021%", "≤ 0.01%", "≤ 0.01%", "FAIL"],
        ["Insoluble matter", "Gravimetric", "0.31%", "≤ 0.02%", "≤ 0.02%", "FAIL"],
    ]

    t = Table(results, colWidths=[80, 70, 56, 65, 65, 55])
    t.setStyle(TableStyle([
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('FONTSIZE', (0, 0), (-1, -1), 8),
        ('BACKGROUND', (0, 0), (-1, 0), BV_DARK),
        ('TEXTCOLOR', (0, 0), (-1, 0), white),
        ('GRID', (0, 0), (-1, -1), 0.5, lightgrey),
        ('ALIGN', (2, 0), (-1, -1), 'CENTER'),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        # Color all FAIL cells red
        ('TEXTCOLOR', (5, 1), (5, -1), red),
        ('FONTNAME', (5, 1), (5, -1), 'Helvetica-Bold'),
        ('ROWBACKGROUNDS', (0, 1), (-1, -1), [white, HexColor("#FFF5F5")]),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
        ('TOPPADDING', (0, 0), (-1, -1), 4),
    ]))
    story.append(t)
    story.append(Spacer(1, 16))

    story.append(Paragraph("<b>COMPARISON WITH SGS PRE-SHIPMENT CERTIFICATE</b>", heading_style))
    compare = [
        ["Parameter", "SGS CoA (CL-ANT-2026-04871)\n2026-01-15 (Origin)", "BV Report (BV-LIM-2026-AN-00618)\n2026-03-15 (Destination)", "Discrepancy"],
        ["Li₂CO₃ purity", "99.71%", "94.2%", "−5.51 pp"],
        ["Moisture (H₂O)", "0.15%", "0.41%", "+0.26 pp"],
        ["Fe content", "12 ppm", "67 ppm", "+55 ppm"],
    ]
    t = Table(compare, colWidths=[90, 130, 135, 70])
    t.setStyle(TableStyle([
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('FONTSIZE', (0, 0), (-1, -1), 8),
        ('BACKGROUND', (0, 0), (-1, 0), BV_DARK),
        ('TEXTCOLOR', (0, 0), (-1, 0), white),
        ('GRID', (0, 0), (-1, -1), 0.5, lightgrey),
        ('ALIGN', (1, 0), (-1, -1), 'CENTER'),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('TEXTCOLOR', (3, 1), (3, -1), red),
        ('FONTNAME', (3, 1), (3, -1), 'Helvetica-Bold'),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
        ('TOPPADDING', (0, 0), (-1, -1), 4),
    ]))
    story.append(t)
    story.append(Spacer(1, 8))
    story.append(Paragraph(
        "A 5.51 percentage-point purity loss between origin and destination is <b>physically "
        "implausible</b> from transit exposure alone. Li<sub>2</sub>CO<sub>3</sub> purity cannot "
        "degrade by more than ~0.3–0.5 pp under normal maritime conditions. The magnitude of "
        "discrepancy suggests either: (a) the goods shipped were not the same lot tested by SGS, "
        "or (b) the SGS certificate does not accurately reflect the actual shipment composition. "
        "Bureau Veritas identifies this as a material documentation inconsistency requiring "
        "independent investigation.", body_style))
    story.append(Spacer(1, 12))

    story.append(Paragraph("<b>CONCLUSION</b>", heading_style))
    story.append(Paragraph(
        "The shipment received under B/L COSU-BOL-2026-003311 (Lot MA-LOT-2026-0119) "
        "<b>clearly and unambiguously DOES NOT MEET</b> the specifications of ISO 6206:2023 "
        "for battery-grade Li<sub>2</sub>CO<sub>3</sub>. The material fails on <b>three "
        "independent critical parameters</b>: purity (94.2% vs ≥ 99.5% contracted), moisture "
        "(0.41% vs ≤ 0.2% contracted), and particle size D90 (52 μm vs ≤ 30 μm contracted). "
        "The material is consistent with technical-grade or lower-grade Li<sub>2</sub>CO<sub>3</sub> "
        "used in industrial applications, not battery cathode manufacturing. "
        "Bureau Veritas recommends the buyer formally reject the entire shipment and initiate "
        "dispute resolution proceedings.", body_style))
    story.append(Spacer(1, 24))

    sig = [["_________________________", "_________________________"],
           ["Dra. Ana Lucía Paredes Vásquez", "Ing. Jorge Castillo Huertas"],
           ["Head of Minerals Laboratory", "Quality Assurance Manager"],
           ["Bureau Veritas del Perú S.A.", "Bureau Veritas del Perú S.A."]]
    t = Table(sig, colWidths=[220, 220])
    t.setStyle(TableStyle([
        ('FONTSIZE', (0, 0), (-1, -1), 9),
        ('FONTNAME', (0, 1), (-1, 1), 'Helvetica-Bold'),
        ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
        ('TOPPADDING', (0, 0), (-1, -1), 2),
    ]))
    story.append(t)

    doc.build(story, onFirstPage=first_page, onLaterPages=first_page)
    print(f"  ✅ {path}")


# ═══════════════════════════════════════════════════════════════════
# Run all
# ═══════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    print("Generating trade finance dispute evidence PDFs...")
    print(f"Output directory: {OUT}\n")
    gen_sgs_coa()
    gen_sgs_inspection()
    gen_bill_of_lading()
    gen_bv_analysis()
    gen_arrival_inspection()
    gen_rejection_notice()
    gen_contract_excerpt()
    gen_bv_analysis_case19()
    print(f"\n✅ All 8 evidence PDFs generated in {OUT}/")
