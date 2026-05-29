"""
CRI E2E Plan — /cri-e2e

Loads CRI issues from Excel (weekly_updates, GT_DCN, KMD tabs) and PPTX
(XPUM/Sysman features) and renders the e2e_plan.html template.
"""
import json
import re
import logging
import openpyxl
from pathlib import Path

from flask import Blueprint, render_template, request, jsonify

from config import CRI_EXCEL, E2E_COMMENTS, HSD_BASE_URL
from utils.cri_helpers import refresh_pptx, extract_jiras, chips, CRI_PPTX

logger = logging.getLogger(__name__)
bp = Blueprint('cri_e2e', __name__)


# ── Comments helpers ──────────────────────────────────────────────────────────

def _load_e2e_comments() -> dict:
    if E2E_COMMENTS.exists():
        with open(E2E_COMMENTS) as f:
            return json.load(f)
    return {}


def _save_e2e_comments(c: dict) -> None:
    with open(E2E_COMMENTS, 'w') as f:
        json.dump(c, f, indent=2)


# ── PPTX XPUM loader (CRI tab) ───────────────────────────────────────────────

def _load_pptx_xpum() -> list[dict]:
    if not CRI_PPTX.exists():
        refresh_pptx()
    if not CRI_PPTX.exists():
        return []
    try:
        from pptx import Presentation
    except ImportError:
        return []

    prs = Presentation(str(CRI_PPTX))
    rows = []
    section_map = {1: 'RAS', 2: 'Telemetry', 3: 'AMC', 4: 'Firmware', 5: 'Opens'}

    for slide_idx, slide in enumerate(prs.slides):
        if slide_idx == 0:
            continue
        section = section_map.get(slide_idx, '')
        for shape in slide.shapes:
            if not shape.has_table:
                continue
            for ri, row in enumerate(shape.table.rows):
                if ri == 0:
                    continue
                cells = [c.text.replace('\xa0', ' ').strip() for c in row.cells]
                if not any(cells):
                    continue
                feature  = cells[0] if len(cells) > 0 else ''
                jira_raw = cells[1] if len(cells) > 1 else ''
                status   = cells[2] if len(cells) > 2 else ''
                dep_raw  = cells[3] if len(cells) > 3 else ''
                if slide_idx == 5:
                    blocker_eta = overall_eta = ''
                    remarks  = cells[3] if len(cells) > 3 else ''
                    dep_raw  = cells[2] if len(cells) > 2 else ''
                else:
                    blocker_eta = cells[4] if len(cells) > 4 else ''
                    overall_eta = cells[5] if len(cells) > 5 else ''
                    remarks     = cells[6] if len(cells) > 6 else ''
                jiras      = extract_jiras(jira_raw) if jira_raw else []
                dep_jiras  = extract_jiras(dep_raw)  if dep_raw  else []
                if not jiras and not feature:
                    continue
                main_jira = jiras[0] if jiras else ''
                rows.append({
                    'section':     section,
                    'feature':     feature,
                    'chips':       chips(jiras),
                    'status':      status,
                    'dep_chips':   chips(dep_jiras),
                    'dep_raw':     dep_raw if not dep_jiras else '',
                    'blocker_eta': blocker_eta,
                    'overall_eta': overall_eta,
                    'remarks':     remarks,
                    'key_bmg':     f'e2e_xbmg_{main_jira or feature[:20]}',
                    'key_cmt':     f'e2e_xcmt_{main_jira or feature[:20]}',
                })
    return rows


# ── Excel CRI loader ──────────────────────────────────────────────────────────

def load_e2e_data() -> tuple[list, list]:
    wb = openpyxl.load_workbook(str(CRI_EXCEL), data_only=True)

    # KMD tab: jira map per HSD
    kmd_jira_map, kmd_title_map = {}, {}
    ws_kmd = wb['KMD']
    for row in ws_kmd.iter_rows(min_row=3, max_col=12, values_only=True):
        if not row[0]:
            continue
        try:
            hid = str(int(row[0]))
        except Exception:
            continue
        if row[1]:
            kmd_title_map[hid] = row[1]
        jiras = []
        for v in row[7:12]:
            jiras += extract_jiras(v)
        kmd_jira_map[hid] = list(dict.fromkeys(jiras))

    # weekly_updates rows 1-46
    ws    = wb['weekly_updates']
    cri_rows, section = [], None
    SKIP  = {'22019854607'}
    for row in ws.iter_rows(min_row=1, max_row=46, min_col=1, max_col=16, values_only=True):
        a = row[0]
        if str(a) in ('sw_only', 'hw+sw'):
            section = str(a)
            continue
        if str(a) in ('ID', 'None') or a is None:
            continue
        try:
            hid = str(int(a))
        except Exception:
            continue
        if hid in SKIP:
            continue
        extra = []
        for v in [row[14], row[15]]:
            extra += extract_jiras(v)
        if hid == '14025305097' and not row[14]:
            extra = ['XPUM-905'] + extra
        all_jiras = list(dict.fromkeys(kmd_jira_map.get(hid, []) + extra))
        cri_rows.append({
            'hsd_id':   hid,
            'hsd_link': HSD_BASE_URL + hid,
            'title':    str(row[1] or kmd_title_map.get(hid, ''))[:120],
            'section':  section or 'sw_only',
            'chips':    chips(all_jiras),
            'key_bmg':  f'e2e_bmg_{hid}',
            'key_cmt':  f'e2e_cmt_{hid}',
        })

    # GT_DCN tab
    ws_gt = wb['GT_DCN']
    for row in ws_gt.iter_rows(min_row=2, max_row=ws_gt.max_row, min_col=1, max_col=4, values_only=True):
        if not row[0]:
            continue
        try:
            hid = str(int(row[0]))
        except Exception:
            continue
        gt_jiras  = extract_jiras(row[3])
        all_jiras = list(dict.fromkeys(kmd_jira_map.get(hid, []) + gt_jiras))
        cri_rows.append({
            'hsd_id':   hid,
            'hsd_link': HSD_BASE_URL + hid,
            'title':    str(row[1] or '')[:120],
            'section':  'gt_dcn',
            'chips':    chips(all_jiras),
            'key_bmg':  f'e2e_bmg_{hid}',
            'key_cmt':  f'e2e_cmt_{hid}',
        })

    # XPUM/Sysman: PPTX primary, Excel fallback
    xpum_rows = _load_pptx_xpum()
    if not xpum_rows:
        if 'xpum' in wb.sheetnames:
            ws_x = wb['xpum']
            cur_feature = ''
            for row in ws_x.iter_rows(min_row=2, max_row=ws_x.max_row, min_col=1, max_col=4, values_only=True):
                if not any(v for v in row):
                    continue
                feature  = str(row[0] or '').strip() or cur_feature
                if row[0]:
                    cur_feature = feature
                jira_raw = str(row[1] or '').strip()
                status   = str(row[2] or '').strip()
                dep_raw  = str(row[3] or '').strip() if len(row) > 3 else ''
                jiras_l  = extract_jiras(jira_raw)
                dep_j    = extract_jiras(dep_raw)
                if not jiras_l:
                    continue
                main_j = jiras_l[0]
                xpum_rows.append({
                    'section': '', 'feature': feature,
                    'chips': chips(jiras_l), 'status': status,
                    'dep_chips': chips(dep_j),
                    'dep_raw': dep_raw if not dep_j else '',
                    'blocker_eta': '', 'overall_eta': '', 'remarks': '',
                    'key_bmg': f'e2e_xbmg_{main_j}',
                    'key_cmt': f'e2e_xcmt_{main_j}',
                })
        else:
            for row in ws.iter_rows(min_row=51, max_row=ws.max_row, min_col=1, max_col=3, values_only=True):
                if not row[0]:
                    continue
                s = str(row[0]).strip()
                if not re.match(r'^[A-Z]+-\d+$', s):
                    continue
                xpum_rows.append({
                    'section': '', 'feature': str(row[1])[:120] if row[1] else s,
                    'chips': chips([s]), 'status': str(row[2]).strip() if row[2] else '',
                    'dep_chips': [], 'dep_raw': '',
                    'blocker_eta': '', 'overall_eta': '', 'remarks': '',
                    'key_bmg': f'e2e_xbmg_{s}', 'key_cmt': f'e2e_xcmt_{s}',
                })

    return cri_rows, xpum_rows


# ── Routes ────────────────────────────────────────────────────────────────────

@bp.route('/cri-e2e')
def cri_e2e():
    cri_rows, xpum_rows = load_e2e_data()
    comments = _load_e2e_comments()
    for r in cri_rows:
        r['bmg']     = comments.get(r['key_bmg'], '')
        r['comment'] = comments.get(r['key_cmt'], '')
    for r in xpum_rows:
        r['bmg']     = comments.get(r['key_bmg'], '')
        r['comment'] = comments.get(r['key_cmt'], '')
    return render_template('e2e_plan.html', cri_rows=cri_rows, xpum_rows=xpum_rows, comments=comments)


@bp.route('/cri-e2e/save', methods=['POST'])
def cri_e2e_save():
    data = request.get_json()
    if not data or 'key' not in data:
        return jsonify({'status': 'error'}), 400
    c = _load_e2e_comments()
    c[data['key']] = data.get('value', '')
    _save_e2e_comments(c)
    return jsonify({'status': 'ok'})
