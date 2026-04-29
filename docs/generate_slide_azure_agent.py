"""Generate animated Azure Agent for Azure Admins PowerPoint deck.

Creates a 3-slide executive presentation:
  Slide 1 — Title & value proposition
  Slide 2 — Architecture diagram (embedded as native shapes)
  Slide 3 — Capabilities summary & extensibility

Run:  python docs/generate_slide_azure_agent.py
"""

from pptx import Presentation
from pptx.util import Inches, Pt, Emu
from pptx.dml.color import RGBColor
from pptx.enum.text import PP_ALIGN, MSO_ANCHOR
from pptx.enum.shapes import MSO_SHAPE
from pptx.oxml.ns import qn
from lxml import etree
import os

# ─── Slide dimensions (16:9) ────────────────────────────────────────────────
SLD_W = Inches(13.333)
SLD_H = Inches(7.5)

# ─── SVG coordinate system for architecture slide ───────────────────────────
SVG_W, SVG_H = 1380, 920


def px(v):
    return int(v * SLD_W / SVG_W)


def py(v):
    return int(v * SLD_H / SVG_H)


# ─── Colour palette ─────────────────────────────────────────────────────────
WHITE = RGBColor(0xFF, 0xFF, 0xFF)
BG = RGBColor(0xF0, 0xF4, 0xF8)
BG_DARK = RGBColor(0x0F, 0x17, 0x2A)
TITLE_DARK = RGBColor(0x1E, 0x29, 0x3B)
SUBTITLE = RGBColor(0x64, 0x74, 0x8B)
ACCENT_BLUE = RGBColor(0x00, 0x78, 0xD4)       # Microsoft blue
ACCENT_BLUE_LT = RGBColor(0xE1, 0xF0, 0xFF)
ACCENT_TEAL = RGBColor(0x00, 0x7A, 0x7A)
ACCENT_GREEN = RGBColor(0x10, 0x7C, 0x10)
ACCENT_GREEN_LT = RGBColor(0xE6, 0xF7, 0xE6)
ACCENT_ORANGE = RGBColor(0xCA, 0x5A, 0x10)
ACCENT_ORANGE_LT = RGBColor(0xFF, 0xF4, 0xE5)
ACCENT_PURPLE = RGBColor(0x5C, 0x2D, 0x91)
ACCENT_PURPLE_LT = RGBColor(0xF3, 0xF0, 0xFF)
ACCENT_RED = RGBColor(0xD1, 0x34, 0x38)
ACCENT_RED_LT = RGBColor(0xFD, 0xF2, 0xF2)
ACCENT_NAVY = RGBColor(0x1B, 0x3A, 0x5C)

# Agent colours
AG_TRIAGE = RGBColor(0x00, 0x78, 0xD4)
AG_OPS = RGBColor(0x10, 0x7C, 0x10)
AG_POLICY = RGBColor(0x5C, 0x2D, 0x91)
AG_QUOTA = RGBColor(0xCA, 0x5A, 0x10)
AG_SUPPORT = RGBColor(0xD1, 0x34, 0x38)
AG_TRIAGE_LT = RGBColor(0xE1, 0xF0, 0xFF)
AG_OPS_LT = RGBColor(0xE6, 0xF7, 0xE6)
AG_POLICY_LT = RGBColor(0xF3, 0xF0, 0xFF)
AG_QUOTA_LT = RGBColor(0xFF, 0xF4, 0xE5)
AG_SUPPORT_LT = RGBColor(0xFD, 0xF2, 0xF2)

# MCP & Backend
MCP_BLUE = RGBColor(0x1E, 0x40, 0xAF)
MCP_LT = RGBColor(0xEF, 0xF6, 0xFF)
MCP_BD = RGBColor(0x3B, 0x82, 0xF6)
MCP_BOX = RGBColor(0xDB, 0xEA, 0xFE)
MCP_BOX_BD = RGBColor(0x60, 0xA5, 0xFA)
MCP_TEXT = RGBColor(0x1E, 0x3A, 0x8A)
AZURE_FILL = RGBColor(0xE1, 0xF0, 0xFF)
AZURE_BD = RGBColor(0x00, 0x78, 0xD4)
AZURE_TEXT = RGBColor(0x00, 0x3B, 0x6A)

GRAY = RGBColor(0x94, 0xA3, 0xB8)
GRAY_LIGHT = RGBColor(0xCB, 0xD5, 0xE1)
ARROW_CLR = RGBColor(0x94, 0xA3, 0xB8)
ARROW_TXT = RGBColor(0x64, 0x74, 0x8B)

FONT = "Segoe UI"

# ─── Presentation ────────────────────────────────────────────────────────────
prs = Presentation()
prs.slide_width = SLD_W
prs.slide_height = SLD_H


# ─── Common helpers ──────────────────────────────────────────────────────────

def _set_bg(slide, color):
    bg = slide.background
    bg.fill.solid()
    bg.fill.fore_color.rgb = color


def _rect(slide, left, top, w, h, fill, bd=None, bd_w=Pt(1), rounded=True):
    typ = MSO_SHAPE.ROUNDED_RECTANGLE if rounded else MSO_SHAPE.RECTANGLE
    s = slide.shapes.add_shape(typ, left, top, w, h)
    s.fill.solid()
    s.fill.fore_color.rgb = fill
    if bd:
        s.line.color.rgb = bd
        s.line.width = bd_w
    else:
        s.line.fill.background()
    if rounded:
        try:
            s.adjustments[0] = 0.06
        except Exception:
            pass
    return s


def _text(slide, left, top, w, h, text, size=10, bold=False, color=TITLE_DARK,
          align=PP_ALIGN.CENTER, anchor=MSO_ANCHOR.MIDDLE):
    tb = slide.shapes.add_textbox(left, top, w, h)
    tf = tb.text_frame
    tf.word_wrap = True
    tf.margin_top = Pt(2)
    tf.margin_bottom = Pt(2)
    tf.margin_left = Pt(4)
    tf.margin_right = Pt(4)
    p = tf.paragraphs[0]
    p.alignment = align
    p.space_before = Pt(0)
    p.space_after = Pt(0)
    r = p.add_run()
    r.text = text
    r.font.size = Pt(size)
    r.font.bold = bold
    r.font.color.rgb = color
    r.font.name = FONT
    return tb


def _mtext(slide, left, top, w, h, lines, align=PP_ALIGN.CENTER):
    tb = slide.shapes.add_textbox(left, top, w, h)
    tf = tb.text_frame
    tf.word_wrap = True
    tf.margin_top = Pt(2)
    tf.margin_bottom = Pt(2)
    tf.margin_left = Pt(4)
    tf.margin_right = Pt(4)
    for i, (text, size, bold, color) in enumerate(lines):
        p = tf.paragraphs[0] if i == 0 else tf.add_paragraph()
        p.alignment = align
        p.space_before = Pt(0)
        p.space_after = Pt(1)
        p.line_spacing = Pt(size + 3)
        r = p.add_run()
        r.text = text
        r.font.size = Pt(size)
        r.font.bold = bold
        r.font.color.rgb = color
        r.font.name = FONT
    return tb


def _arrow(slide, x1, y1, x2, y2, color=ARROW_CLR, w=Pt(2)):
    cx = slide.shapes.add_connector(1, x1, y1, x2, y2)
    cx.line.color.rgb = color
    cx.line.width = w
    ln = cx.line._ln
    tail = ln.makeelement(qn('a:tailEnd'),
                          {'type': 'triangle', 'w': 'med', 'len': 'med'})
    ln.append(tail)
    return cx


def _line(slide, x1, y1, x2, y2, color=ARROW_CLR, w=Pt(1.5), dashed=False):
    cx = slide.shapes.add_connector(1, x1, y1, x2, y2)
    cx.line.color.rgb = color
    cx.line.width = w
    if dashed:
        cx.line.dash_style = 4
    return cx


def _layer_frame(slide, x, y, w, h, bh, banner_clr, border_clr, label):
    _rect(slide, px(x), py(y), px(w), py(h), WHITE, border_clr, Pt(1.5))
    _rect(slide, px(x), py(y), px(w), py(bh), banner_clr, banner_clr, Pt(0))
    _rect(slide, px(x), py(y + bh - 8), px(w), py(10),
          banner_clr, banner_clr, Pt(0), rounded=False)
    _text(slide, px(x), py(y + 2), px(w), py(bh - 4), label,
          size=12, bold=True, color=WHITE)


def _icon_card(slide, x, y, w, h, icon, label, fill, bd, text_clr):
    _rect(slide, px(x), py(y), px(w), py(h), fill, bd)
    _mtext(slide, px(x), py(y), px(w), py(h), [
        (icon, 14, False, text_clr),
        (label, 9, True, text_clr),
    ])


# ═════════════════════════════════════════════════════════════════════════════
# SLIDE 1 — Title & Value Proposition
# ═════════════════════════════════════════════════════════════════════════════
s1 = prs.slides.add_slide(prs.slide_layouts[6])
_set_bg(s1, BG_DARK)

# Accent stripe
_rect(s1, Inches(0), Inches(0), Inches(0.12), SLD_H,
      ACCENT_BLUE, rounded=False)

# Title
_text(s1, Inches(1), Inches(1.0), Inches(11), Inches(0.7),
      "Azure Agent for Azure Admins", size=36, bold=True, color=WHITE,
      align=PP_ALIGN.LEFT)

# Subtitle
_text(s1, Inches(1), Inches(1.75), Inches(10), Inches(0.5),
      "Conversational AI that simplifies Azure operations — with near-zero friction",
      size=18, color=RGBColor(0x94, 0xA3, 0xB8), align=PP_ALIGN.LEFT)

# Divider
_rect(s1, Inches(1), Inches(2.5), Inches(3), Inches(0.04),
      ACCENT_BLUE, rounded=False)

# Pain points column
pain_y = Inches(3.0)
_text(s1, Inches(1), pain_y, Inches(5), Inches(0.4),
      "THE PROBLEM", size=12, bold=True, color=ACCENT_BLUE, align=PP_ALIGN.LEFT)

pains = [
    "Azure Admins spend hours daily on repetitive resource, cost & policy tasks",
    "Multiple portal blades, CLI commands, and docs to achieve simple goals",
    "Quota requests & support tickets involve manual multi-step workflows",
    "No unified view across resources, costs, health, and compliance",
]
for i, txt in enumerate(pains):
    _text(s1, Inches(1), pain_y + Inches(0.45 + i * 0.42), Inches(5.2), Inches(0.4),
          f"▸  {txt}", size=13, color=RGBColor(0xCB, 0xD5, 0xE1),
          align=PP_ALIGN.LEFT)

# Solution column
_text(s1, Inches(7), pain_y, Inches(5.5), Inches(0.4),
      "THE SOLUTION", size=12, bold=True, color=ACCENT_GREEN, align=PP_ALIGN.LEFT)

solns = [
    "Natural-language interface — ask questions, get answers & actions",
    "6 specialised AI agents that route, reason & execute with high accuracy",
    "Unified dashboard: resources, costs, health, compliance in one view",
    "Extensible MCP architecture — admins add custom tools in minutes",
]
for i, txt in enumerate(solns):
    _text(s1, Inches(7), pain_y + Inches(0.45 + i * 0.42), Inches(5.5), Inches(0.4),
          f"✓  {txt}", size=13, color=RGBColor(0xA7, 0xF3, 0xD0),
          align=PP_ALIGN.LEFT)

# Bottom tagline
_text(s1, Inches(1), Inches(6.5), Inches(11), Inches(0.5),
      "Built on Microsoft Agent Framework  ·  MCP Protocol  ·  Azure REST APIs  ·  Entra ID",
      size=11, color=RGBColor(0x64, 0x74, 0x8B), align=PP_ALIGN.CENTER)


# ═════════════════════════════════════════════════════════════════════════════
# SLIDE 2 — Architecture Diagram
# ═════════════════════════════════════════════════════════════════════════════
s2 = prs.slides.add_slide(prs.slide_layouts[6])
_set_bg(s2, BG)

# Title
_text(s2, px(20), py(8), px(900), py(36),
      "Azure Agent — Architecture Overview", size=20, bold=True,
      color=TITLE_DARK, align=PP_ALIGN.LEFT)

# ── Layer 1: User Interfaces ────────────────────────────────────────────────
_layer_frame(s2, 60, 55, 1090, 95, 26,
             RGBColor(0x05, 0x96, 0x69), RGBColor(0x05, 0x96, 0x69),
             "USER INTERFACES")

ui_items = [
    (110,  "🌐", "Web SPA\n(React + MSAL)"),
    (310,  "💬", "Chat\nInterface"),
    (510,  "📊", "Dashboard\nView"),
    (710,  "📷", "Image\nAnalysis"),
    (910,  "⚙️", "CLI /\nAutomation"),
]
for ux, icon, label in ui_items:
    _rect(s2, px(ux), py(92), px(160), py(48),
          RGBColor(0xEC, 0xFD, 0xF5), RGBColor(0x10, 0xB9, 0x81))
    _mtext(s2, px(ux), py(92), px(160), py(48), [
        (icon, 12, False, RGBColor(0x06, 0x5F, 0x46)),
        (label, 9, True, RGBColor(0x06, 0x5F, 0x46)),
    ])

# Arrow: UI → Agent
_arrow(s2, px(600), py(150), px(600), py(172))
_text(s2, px(615), py(152), px(180), py(16),
      "Entra ID Auth + NDJSON", size=8, color=ARROW_TXT, align=PP_ALIGN.LEFT)

# ── Layer 2: Agent Orchestration ─────────────────────────────────────────────
_layer_frame(s2, 60, 175, 1090, 180, 26,
             RGBColor(0x1E, 0x40, 0xAF), RGBColor(0x1E, 0x40, 0xAF),
             "AGENT ORCHESTRATION  (Microsoft Agent Framework)")

# Triage agent (centred at top)
_rect(s2, px(430), py(210), px(340), py(40),
      AG_TRIAGE_LT, AG_TRIAGE, Pt(1.5))
_mtext(s2, px(430), py(212), px(340), py(36), [
    ("🧠  Triage Agent — Intent Analysis & Routing", 10, True, AG_TRIAGE),
])

# Handoff arrows
agents_x = [100, 320, 540, 760, 980]
for ax in agents_x:
    _arrow(s2, px(600), py(250), px(ax + 70), py(272),
           color=RGBColor(0x60, 0xA5, 0xFA), w=Pt(1.2))

# Specialist agents
agent_defs = [
    (100,  "🔧", "Azure Ops\nAgent", "Resources · Costs\nMetrics · Reports",
     AG_OPS_LT, AG_OPS),
    (320,  "📋", "Policy\nAgent", "Compliance\nGovernance",
     AG_POLICY_LT, AG_POLICY),
    (540,  "📊", "Quota List\nAgent", "Limits by\nProvider & Region",
     AG_QUOTA_LT, AG_QUOTA),
    (760,  "📈", "Quota Request\nAgent", "Increase Requests\n& Tracking",
     AG_QUOTA_LT, AG_QUOTA),
    (980,  "🎫", "Support\nAgent", "Tickets &\nCommunications",
     AG_SUPPORT_LT, AG_SUPPORT),
]
for ax, icon, name, desc, fill, clr in agent_defs:
    _rect(s2, px(ax), py(272), px(180), py(72),
          fill, clr, Pt(1.2))
    _mtext(s2, px(ax), py(274), px(180), py(70), [
        (icon, 10, False, clr),
        (name, 9, True, clr),
        (desc, 7, False, clr),
    ])

# Arrow: Agents → MCP
_arrow(s2, px(600), py(355), px(600), py(380))
_text(s2, px(615), py(358), px(180), py(16),
      "SSE / Streamable HTTP", size=8, color=ARROW_TXT, align=PP_ALIGN.LEFT)

# ── Layer 3: MCP Server (Tool Layer) ────────────────────────────────────────
_layer_frame(s2, 60, 383, 1090, 175, 26,
             MCP_BLUE, MCP_BD, "MCP SERVER — TOOL LAYER  (FastMCP)")

tools_row1 = [
    (85,  "🔍", "Resource\nGraph", MCP_BOX, MCP_BOX_BD, MCP_TEXT),
    (230, "💰", "Cost\nAnalysis", MCP_BOX, MCP_BOX_BD, MCP_TEXT),
    (375, "📈", "Monitoring\n& Health", MCP_BOX, MCP_BOX_BD, MCP_TEXT),
    (520, "📋", "Policy &\nCompliance", MCP_BOX, MCP_BOX_BD, MCP_TEXT),
    (665, "📊", "Quota\nMgmt", MCP_BOX, MCP_BOX_BD, MCP_TEXT),
    (810, "🎫", "Support\nTickets", MCP_BOX, MCP_BOX_BD, MCP_TEXT),
    (955, "🔧", "Resource\nOps", MCP_BOX, MCP_BOX_BD, MCP_TEXT),
]
for tx, icon, label, fill, bd, tc in tools_row1:
    _rect(s2, px(tx), py(418), px(128), py(55), fill, bd)
    _mtext(s2, px(tx), py(420), px(128), py(52), [
        (icon, 10, False, tc),
        (label, 8, True, tc),
    ])

tools_row2 = [
    (85,  "📧", "Email\nNotify", MCP_BOX, MCP_BOX_BD, MCP_TEXT),
    (230, "📄", "Report\nGen", MCP_BOX, MCP_BOX_BD, MCP_TEXT),
    (375, "🏷️", "Tag\nMgmt", MCP_BOX, MCP_BOX_BD, MCP_TEXT),
    (520, "💻", "VM Power\nOps", MCP_BOX, MCP_BOX_BD, MCP_TEXT),
    (665, "🔎", "Orphan\nDetection", MCP_BOX, MCP_BOX_BD, MCP_TEXT),
    (810, "⏸️", "Idle Resource\nDetection", MCP_BOX, MCP_BOX_BD, MCP_TEXT),
]
for tx, icon, label, fill, bd, tc in tools_row2:
    _rect(s2, px(tx), py(482), px(128), py(55), fill, bd)
    _mtext(s2, px(tx), py(484), px(128), py(52), [
        (icon, 10, False, tc),
        (label, 8, True, tc),
    ])

# Extensibility callout
_rect(s2, px(955), py(482), px(128), py(55),
      RGBColor(0xFE, 0xF3, 0xC7), RGBColor(0xFB, 0xBF, 0x24), Pt(1.5))
_mtext(s2, px(955), py(484), px(128), py(52), [
    ("➕", 12, False, RGBColor(0x92, 0x40, 0x0E)),
    ("Your Custom\nTools", 8, True, RGBColor(0x92, 0x40, 0x0E)),
])

# Arrow: MCP → Azure
_arrow(s2, px(600), py(558), px(600), py(585))
_text(s2, px(615), py(562), px(200), py(16),
      "Azure REST APIs (Bearer Token)", size=8, color=ARROW_TXT, align=PP_ALIGN.LEFT)

# ── Layer 4: Azure Services ─────────────────────────────────────────────────
_layer_frame(s2, 60, 590, 1090, 120, 26,
             ACCENT_BLUE, AZURE_BD, "AZURE SERVICES")

azure_svcs = [
    (85,  "ARM\nResources"),
    (210, "Cost\nManagement"),
    (335, "Azure\nMonitor"),
    (460, "Resource\nGraph"),
    (585, "Azure\nPolicy"),
    (710, "Quota\nAPI"),
    (835, "Support\nAPI"),
    (960, "Entra ID\n(Auth)"),
]
for ax, label in azure_svcs:
    _rect(s2, px(ax), py(626), px(110), py(72),
          AZURE_FILL, AZURE_BD)
    _mtext(s2, px(ax), py(630), px(110), py(66), [
        ("☁️", 10, False, AZURE_TEXT),
        (label, 8, True, AZURE_TEXT),
    ])

# ── Sidebar: Security & Auth ────────────────────────────────────────────────
_rect(s2, px(1170), py(55), px(190), py(655), WHITE,
      RGBColor(0x00, 0x78, 0xD4), Pt(1.5))
_rect(s2, px(1170), py(55), px(190), py(26),
      ACCENT_BLUE, ACCENT_BLUE, Pt(0))
_rect(s2, px(1170), py(71), px(190), py(10),
      ACCENT_BLUE, ACCENT_BLUE, Pt(0), rounded=False)
_text(s2, px(1170), py(57), px(190), py(24),
      "SECURITY & AUTH", size=11, bold=True, color=WHITE)

sec_items = [
    (96,  "🔐", "Entra ID SSO", "MSAL + PKCE"),
    (166, "🎟️", "Token Flow", "On-Behalf-Of"),
    (236, "🛡️", "Azure RBAC", "Least Privilege"),
    (306, "📝", "Audit Trail", "Activity Logging"),
    (376, "🔒", "JWT Validation", "FastAPI Middleware"),
    (446, "🌐", "CORS Policy", "SPA ↔ API"),
]
for sy, icon, title, sub in sec_items:
    _rect(s2, px(1185), py(sy), px(160), py(58),
          ACCENT_BLUE_LT, AZURE_BD, Pt(0.8))
    _mtext(s2, px(1185), py(sy + 2), px(160), py(54), [
        (icon, 10, False, ACCENT_BLUE),
        (title, 9, True, ACCENT_BLUE),
        (sub, 7, False, AZURE_TEXT),
    ])

# Dashed lines from layers to sidebar
for dy in [110, 270, 460, 650]:
    _line(s2, px(1150), py(dy), px(1170), py(dy),
          AZURE_BD, Pt(1), dashed=True)

# Flow labels on left margin
flows = [
    (110, "USERS"),
    (280, "AI AGENTS"),
    (470, "TOOL LAYER"),
    (650, "CLOUD APIs"),
]
for fy, txt in flows:
    _text(s2, px(5), py(fy - 12), px(52), py(28), txt,
          size=7, bold=True, color=ARROW_TXT)

# Legend bar
_rect(s2, px(60), py(722), px(1090), py(30), WHITE, GRAY_LIGHT, Pt(1))
_text(s2, px(62), py(724), px(65), py(26), "LEGEND:",
      size=9, bold=True, color=SUBTITLE, align=PP_ALIGN.RIGHT)

legend = [
    (140, RGBColor(0xEC, 0xFD, 0xF5), RGBColor(0x10, 0xB9, 0x81), "UI Layer"),
    (225, AG_TRIAGE_LT, AG_TRIAGE, "Triage"),
    (310, AG_OPS_LT, AG_OPS, "Ops Agent"),
    (400, AG_POLICY_LT, AG_POLICY, "Policy"),
    (478, AG_QUOTA_LT, AG_QUOTA, "Quota"),
    (555, AG_SUPPORT_LT, AG_SUPPORT, "Support"),
    (640, MCP_BOX, MCP_BOX_BD, "MCP Tools"),
    (735, RGBColor(0xFE, 0xF3, 0xC7), RGBColor(0xFB, 0xBF, 0x24), "Extensible"),
    (830, AZURE_FILL, AZURE_BD, "Azure APIs"),
    (920, ACCENT_BLUE_LT, AZURE_BD, "Security"),
]
for lx, fc, bc, label in legend:
    _rect(s2, px(lx), py(728), px(12), py(12), fc, bc, Pt(1))
    _text(s2, px(lx + 15), py(725), px(75), py(20), label,
          size=8, color=SUBTITLE, align=PP_ALIGN.LEFT)


# ═════════════════════════════════════════════════════════════════════════════
# SLIDE 3 — Capabilities & Extensibility
# ═════════════════════════════════════════════════════════════════════════════
s3 = prs.slides.add_slide(prs.slide_layouts[6])
_set_bg(s3, WHITE)

# Accent stripe
_rect(s3, Inches(0), Inches(0), SLD_W, Inches(0.08),
      ACCENT_BLUE, rounded=False)

_text(s3, Inches(0.6), Inches(0.3), Inches(12), Inches(0.6),
      "What Azure Agent Does for You", size=28, bold=True,
      color=TITLE_DARK, align=PP_ALIGN.LEFT)

_text(s3, Inches(0.6), Inches(0.85), Inches(12), Inches(0.3),
      "40+ MCP tools across 9 domains — all accessible through natural language",
      size=14, color=SUBTITLE, align=PP_ALIGN.LEFT)

# Capability cards — 3 columns × 3 rows
caps = [
    ("🔍", "Resource Discovery", "List, search & inspect resources\nusing KQL Resource Graph queries",
     ACCENT_BLUE, ACCENT_BLUE_LT),
    ("💰", "Cost Analysis", "Cost summaries by RG, service,\nresource — up to 90-day windows",
     ACCENT_GREEN, ACCENT_GREEN_LT),
    ("📈", "Monitoring & Health", "Metrics, resource health,\nactivity logs & idle detection",
     ACCENT_TEAL, RGBColor(0xE0, 0xF7, 0xF7)),
    ("📋", "Policy Governance", "List assignments, check compliance,\nauthor & assign custom policies",
     ACCENT_PURPLE, ACCENT_PURPLE_LT),
    ("📊", "Quota Management", "View limits, request increases,\ntrack approval across providers",
     ACCENT_ORANGE, ACCENT_ORANGE_LT),
    ("🎫", "Support Tickets", "Create, update & track tickets;\nmanage communications lifecycle",
     ACCENT_RED, ACCENT_RED_LT),
    ("📄", "Interactive Reports", "Auto-generate HTML dashboards\nwith charts, tables & filters",
     ACCENT_NAVY, RGBColor(0xE8, 0xEE, 0xF5)),
    ("🏷️", "Resource Management", "VM power ops, tag updates,\norphan & idle resource detection",
     RGBColor(0x0E, 0x7C, 0x86), RGBColor(0xE0, 0xF7, 0xF7)),
    ("📧", "Email Notifications", "Send resource findings\nto owners & stakeholders",
     RGBColor(0x6D, 0x28, 0xD9), RGBColor(0xF3, 0xF0, 0xFF)),
]

card_w = Inches(3.7)
card_h = Inches(1.3)
start_x = Inches(0.6)
start_y = Inches(1.5)
gap_x = Inches(0.25)
gap_y = Inches(0.18)

for i, (icon, title, desc, clr, fill) in enumerate(caps):
    col = i % 3
    row = i // 3
    cx = start_x + col * (card_w + gap_x)
    cy = start_y + row * (card_h + gap_y)

    # Card
    _rect(s3, cx, cy, card_w, card_h, fill, clr, Pt(1.2))
    # Accent bar on left
    _rect(s3, cx, cy, Inches(0.06), card_h, clr, clr, Pt(0), rounded=False)
    # Icon + title
    _text(s3, cx + Inches(0.2), cy + Inches(0.12), card_w - Inches(0.3), Inches(0.35),
          f"{icon}  {title}", size=14, bold=True, color=clr, align=PP_ALIGN.LEFT)
    # Description
    _text(s3, cx + Inches(0.2), cy + Inches(0.5), card_w - Inches(0.3), Inches(0.7),
          desc, size=11, color=TITLE_DARK, align=PP_ALIGN.LEFT)

# Extensibility banner
banner_y = start_y + 3 * (card_h + gap_y) + Inches(0.15)
_rect(s3, Inches(0.6), banner_y, Inches(11.8), Inches(1.1),
      RGBColor(0xFE, 0xF3, 0xC7), RGBColor(0xFB, 0xBF, 0x24), Pt(2))
_rect(s3, Inches(0.6), banner_y, Inches(0.08), Inches(1.1),
      RGBColor(0xF5, 0x9E, 0x0B), RGBColor(0xF5, 0x9E, 0x0B), Pt(0), rounded=False)

_text(s3, Inches(1.0), banner_y + Inches(0.12), Inches(6), Inches(0.35),
      "➕  Extensible by Design", size=16, bold=True,
      color=RGBColor(0x78, 0x35, 0x0F), align=PP_ALIGN.LEFT)
_text(s3, Inches(1.0), banner_y + Inches(0.5), Inches(10.5), Inches(0.5),
      "Azure Admins can add custom MCP tools for their specific needs — proprietary APIs, internal databases, "
      "compliance checks, or automation workflows. Simply define a Python function, register it as an MCP tool, "
      "and the agent picks it up automatically.",
      size=12, color=RGBColor(0x92, 0x40, 0x0E), align=PP_ALIGN.LEFT)


# ─── Save ────────────────────────────────────────────────────────────────────
out = os.path.join(os.path.dirname(__file__), "azure_agent_for_admins.pptx")
prs.save(out)
print(f"Saved → {out}")
print(f"  3 slides, executive layout")
