import sys
import os
import re
import requests
from datetime import datetime
import difflib

from PyQt6.QtGui import QPixmap, QFont, QPainter, QColor
from PyQt6.QtCore import Qt, QSize, QRect, QPropertyAnimation, pyqtSignal, QSettings
from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QSplitter, QLabel, QLineEdit,
    QPushButton, QListWidget, QListWidgetItem, QFileDialog, QVBoxLayout,
    QHBoxLayout, QFormLayout, QMessageBox, QSpinBox, QCheckBox, QDialog,
    QSizePolicy, QListView, QToolButton, QFrame, QGroupBox, QGridLayout,
    QComboBox, QRadioButton, QCheckBox, QLabel, QLineEdit, QGraphicsBlurEffect
)

# -------------------- helpers --------------------
def normalize_string(s: str) -> str:
    return re.sub(r'\W+', '', s).lower()

def get_confidence_color(match_val: float) -> str:
    p = max(0.0, min(1.0, match_val))
    if p <= 0.5:
        t = p / 0.5
        r, g, b = 255, int(t * 165), 0
    else:
        t = (p - 0.5) / 0.5
        r, g, b = int(255 * (1 - t)), int(165 + t * (255 - 165)), 0
    return f"#{r:02X}{g:02X}{b:02X}"

relation_map = {
    "char": "Character",
    "fan": "Fandisc",
    "preq": "Prequel",
    "seq": "Sequel",
    "ser": "Same Series",
    "set": "Same Setting",
    "side": "Side Story",
}

def create_vndb_shortcut(folder_path: str, vn_id: str) -> None:
    url = f"https://vndb.org/{vn_id}"
    try:
        with open(os.path.join(folder_path, "VN Shortcut.url"), "w", encoding="utf-8") as f:
            f.write(f"[InternetShortcut]\nURL={url}\n")
    except Exception as e:
        print("Error creating VNDB shortcut:", e)

def normalize_censor_mode(mode: str | None) -> str:
    """Map UI text ('Blur', 'Cover', etc.) to internal keys."""
    m = (mode or "").strip().lower()
    if m in ("blur",): return "blur"
    if m in ("cover", "box", "black"): return "box"
    if m in ("pixelate", "pixel", "mosaic"): return "pixelate"
    return "blur"  # default

def is_adult_from_imageinfo(img: dict | None) -> bool:
    if not isinstance(img, dict): return False
    sexual = float(img.get("sexual"))
    violence = float(img.get("violence"))
    return (sexual >= 1) or (violence >= 1)

def is_adult_candidate(cand: dict) -> bool:
    return is_adult_from_imageinfo(cand.get("image"))

def is_adult_relation(rel: dict) -> bool:
    return is_adult_from_imageinfo(rel.get("image"))

def pixelate_pixmap(pm: QPixmap, factor: int = 12) -> QPixmap:
    w, h = pm.width(), pm.height()
    if w <= 0 or h <= 0:
        return pm
    small = pm.scaled(max(1, w // factor), max(1, h // factor),
                      Qt.AspectRatioMode.KeepAspectRatio,
                      Qt.TransformationMode.FastTransformation)
    return small.scaled(w, h,
                        Qt.AspectRatioMode.KeepAspectRatio,
                        Qt.TransformationMode.FastTransformation)

def apply_censor_to_label(label: QLabel, pm: QPixmap,
                          enabled: bool = True,
                          mode: str = "blur",
                          is_adult: bool = False) -> None:
    """Set pixmap; if adult & enabled, apply blur/cover. Default = blur."""
    label.setGraphicsEffect(None)

    if not (enabled and is_adult):
        label.setPixmap(pm)
        return

    m = normalize_censor_mode(mode)

    if m == "blur":
        label.setPixmap(pm)
        eff = QGraphicsBlurEffect()
        eff.setBlurRadius(12)
        label.setGraphicsEffect(eff)
    elif m == "box":
        # draw a semi-opaque black cover on top
        over = QPixmap(pm.size())
        over.fill(Qt.GlobalColor.transparent)
        p = QPainter(over)
        p.drawPixmap(0, 0, pm)
        p.fillRect(over.rect(), QColor(0, 0, 0, 220))
        # “18+” badge (top-left)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        h = over.height()
        pad = max(6, h // 40)
        bw, bh = max(40, h // 8), max(24, h // 12)
        badge = QRect(pad, pad, bw, bh)

        p.setBrush(QColor(200, 0, 0, 235))
        p.setPen(Qt.PenStyle.NoPen)
        p.drawRoundedRect(badge, 6, 6)

        p.setPen(Qt.GlobalColor.white)
        f: QFont = p.font()
        f.setBold(True)
        f.setPointSize(max(10, h // 18))
        p.setFont(f)
        p.drawText(badge, Qt.AlignmentFlag.AlignCenter, "18+")
        p.end()
        label.setPixmap(over)
    else:
        # keep as fallback if you ever re-enable pixelate
        label.setPixmap(pm)
        eff = QGraphicsBlurEffect()
        eff.setBlurRadius(12)
        label.setGraphicsEffect(eff)

# -------------------- UI widgets --------------------
class CollapsiblePanel(QWidget):
    def __init__(self, title: str = "", parent=None):
        super().__init__(parent)
        self.toggle_button = QToolButton(text=title, checkable=True, checked=False)
        self.toggle_button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.toggle_button.setArrowType(Qt.ArrowType.RightArrow)
        self.toggle_button.clicked.connect(self.on_toggle)
        self.content_area = QFrame()
        self.content_area.setMaximumHeight(0)
        self.content_area.setMinimumHeight(0)
        self.anim = QPropertyAnimation(self.content_area, b"maximumHeight", self)
        self.anim.setDuration(200)
        lay = QVBoxLayout(self)
        lay.setSpacing(0); lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self.toggle_button)
        lay.addWidget(self.content_area)

    def on_toggle(self, checked: bool):
        self.toggle_button.setArrowType(Qt.ArrowType.DownArrow if checked else Qt.ArrowType.RightArrow)
        end_h = self.content_area.layout().sizeHint().height() if checked else 0
        self.anim.setEndValue(end_h)
        self.anim.start()

    def setContentLayout(self, layout):
        self.content_area.setLayout(layout)

class CustomTemplateEditor(QListWidget):
    templateChanged = pyqtSignal()
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFlow(QListView.Flow.LeftToRight)
        self.setWrapping(False)
        self.setResizeMode(QListView.ResizeMode.Adjust)
        self.setDragDropMode(QListWidget.DragDropMode.InternalMove)
        self.setDefaultDropAction(Qt.DropAction.MoveAction)
        self.setMaximumHeight(80)
        for elem in ["producer", "release_date", "length", "title", "flags", "tags"]:
            it = QListWidgetItem(elem)
            it.setFlags(it.flags() | Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
            it.setCheckState(Qt.CheckState.Checked)
            self.addItem(it)
        self.itemChanged.connect(lambda _: self.templateChanged.emit())

    def dropEvent(self, e):
        super().dropEvent(e)
        self.templateChanged.emit()

    def getTemplate(self) -> str:
        parts = []
        for i in range(self.count()):
            item = self.item(i)
            if item.checkState() != Qt.CheckState.Checked:
                continue
            t = item.text()
            parts.append({
                "producer": "[{producer}]",
                "release_date": "[{release_date}]",
                "length": "{length}",
                "title": "{title}",
                "flags": "{flags}",
                "tags": "({tags})",
            }.get(t, "{" + t + "}"))
        return " ".join(parts)

# -------------------- parsing --------------------
def parse_folder_name(folder_name: str):
    cleaned = re.sub(r'\[.*?\]', '', folder_name)
    cleaned = re.sub(r'\(.*?\)', '', cleaned)
    cleaned = re.sub(r'\{.*?\}', '', cleaned)
    return {"title": cleaned.strip(), "release": ""}

def parse_bracket_info(folder_name: str):
    matches = re.findall(r'\[(.*?)\]', folder_name)
    expected_date = ""; expected_producer = ""
    for segment in matches:
        segment = segment.strip()
        if segment.isdigit() and len(segment) == 6:
            try:
                mm = int(segment[2:4]); dd = int(segment[4:6])
                if 1 <= mm <= 12 and 1 <= dd <= 31:
                    expected_date = segment
            except ValueError:
                pass
        elif len(segment) >= 3 and not (segment.isdigit() and len(segment) > 6) and not expected_producer:
            expected_producer = segment
    return expected_date, expected_producer

# -------------------- API --------------------
def _post(url: str, payload: dict):
    headers = {"Content-Type": "application/json"}
    try:
        r = requests.post(url, headers=headers, json=payload, timeout=10)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        print("API error:", e)
        return None

def get_vn_candidates(search_query, expected_date="", expected_producer="", candidate_limit=5):
    payload = {
        "filters": ["search", "=", search_query],
        "fields": ("id,title,titles.title,released,developers.name,olang,length,"
                   "image.url,image.sexual,image.violence,rating,"
                   "relations.id,relations.relation,relations.title,relations.relation_official,"
                   "relations.image.url,relations.image.sexual,relations.image.violence,relations.developers.name")
    }
    data = _post("https://api.vndb.org/kana/vn", payload)
    if not data or not isinstance(data.get("results"), list):
        return []
    results = []
    for cand in data["results"]:
        romaji = cand.get("title", "")
        sim_romaji = difflib.SequenceMatcher(None, search_query.lower(), romaji.lower()).ratio()
        sim_jp = 0.0
        for t in cand.get("titles", []):
            orig = t.get("title", "")
            sim_jp = max(sim_jp, difflib.SequenceMatcher(None, search_query.lower(), orig.lower()).ratio())
        title_sim = max(sim_romaji, sim_jp)

        date_sim = 0.5
        rel = cand.get("released", "")
        if expected_date and rel:
            if len(expected_date) == 6 and expected_date.isdigit():
                norm = f"20{expected_date[:2]}-{expected_date[2:4]}-{expected_date[4:6]}"
            else:
                norm = expected_date
            date_sim = difflib.SequenceMatcher(None, norm, rel).ratio()

        devs = cand.get("developers", [])
        devname = devs[0].get("name", "") if devs and isinstance(devs[0], dict) else (devs[0] if devs else "")
        dev_sim = 0.5
        if expected_producer and devname:
            dev_sim = difflib.SequenceMatcher(None, normalize_string(expected_producer), normalize_string(devname)).ratio()

        cand["match"] = (title_sim + date_sim + dev_sim) / 3.0
        results.append(cand)

    results.sort(key=lambda c: c["match"], reverse=True)
    return results[:candidate_limit]

def search_release(vn_id: str):
    """
    Try to fetch official releases for the VN.
    If nothing comes back, return None (and we’ll just use VN-level data).
    """
    # Try both "v####" and numeric id; return first hit
    vn_num = vn_id[1:] if str(vn_id).startswith("v") else str(vn_id)
    for value in (f"v{vn_num}", vn_num):
        data = _post("https://api.vndb.org/kana/release", {
            "filters": ["vn", "=", value],
            "fields": "title,released,producers.name,producers.developer,official,minage"
        })
        if data and isinstance(data.get("results"), list) and data["results"]:
            for rel in data["results"]:
                if rel.get("official"):
                    return rel
            return data["results"][0]
    return None

# -------------------- naming --------------------
def suggest_new_folder_name(vn_info, release_info, optional_flags=None, optional_tags=None, custom_template=None, title_override=None):
    producer = "Unknown Producer"
    if release_info and release_info.get("producers"):
        for p in release_info["producers"]:
            if p.get("developer"):
                producer = p.get("name", "Unknown Producer")
                break
        else:
            producer = release_info["producers"][0].get("name", "Unknown Producer")
    else:
        devs = vn_info.get("developers", [])
        if devs:
            producer = devs[0].get("name", "Unknown Producer") if isinstance(devs[0], dict) else devs[0]

    date_str = release_info.get("released") if (release_info and release_info.get("released")) else vn_info.get("released", "")

    def fmt(ds):
        try: return datetime.strptime(ds, "%Y-%m-%d").strftime("%y%m%d")
        except Exception: return "Unknown"

    formatted_date = fmt(date_str) if date_str else "Unknown"
    length_str = f"[L{vn_info.get('length')}]" if vn_info.get("length") is not None else ""
    title_str = title_override or vn_info.get("title", "Unknown Title")
    flags_str = (optional_flags or "").strip()
    tags_str  = (optional_tags  or "").strip()

    if custom_template and custom_template.strip():
        try:
            name = custom_template.format(
                producer=producer, release_date=formatted_date, length=length_str,
                title=title_str, flags=flags_str, tags=tags_str
            )
            name = re.sub(r"\(\s*\)", "", name).strip()
            return name
        except KeyError as e:
            return f"Error: missing placeholder {e}"

    name = f"[{producer}][{formatted_date}]{length_str} {title_str}"
    if flags_str: name += f" {{{flags_str}}}"
    if tags_str:  name += f" ({tags_str})"
    return name

# -------------------- dialogs --------------------
class CandidateDetailDialog(QDialog):
    def __init__(self, candidate, censor_enabled=True, censor_mode="pixelate", parent=None):
        super().__init__(parent)
        self.setWindowTitle("Candidate Details")
        self.resize(820, 520)
        self.candidate = candidate
        self.censor_enabled = censor_enabled
        self.censor_mode = censor_mode
        self.match_val = candidate.get("match", 0.0)

        _MINAGE_CACHE = {}

        def get_minage_for_vn(vnid) -> int:
            v = f"v{vnid}" if vnid and not str(vnid).startswith("v") else str(vnid or "")
            if not v:
                return 0
            if v in _MINAGE_CACHE:
                return _MINAGE_CACHE[v]
            rel = search_release(v)
            val = int((rel or {}).get("minage") or 0)
            _MINAGE_CACHE[v] = val
            return val

        def is_adult_vn(vnid) -> bool:
            return get_minage_for_vn(vnid) >= 18

        outer = QVBoxLayout(self)

        info = QHBoxLayout(); info.setSpacing(12)
        # Image
        self.img_label = QLabel()
        self.img_label.setFixedSize(300, 300)
        img_info = self.candidate.get("image", {})
        if isinstance(img_info, dict) and "url" in img_info:
            try:
                response = requests.get(img_info["url"], stream=True, timeout=10)
                response.raise_for_status()
                pm = QPixmap()
                pm.loadFromData(response.content)
                pm = pm.scaled(300, 300, Qt.AspectRatioMode.KeepAspectRatio,
                               Qt.TransformationMode.SmoothTransformation)
                apply_censor_to_label(
                    self.img_label,
                    pm,
                    enabled=getattr(self, "censor_enabled", True),
                    mode=normalize_censor_mode(getattr(self, "censor_mode", "blur")),
                    is_adult=is_adult_candidate(self.candidate)
                )
            except Exception as e:
                print("Image error:", e)
        info.addWidget(self.img_label, alignment=Qt.AlignmentFlag.AlignTop)

        # Text
        text = QVBoxLayout(); text.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        title = QLabel(self.candidate.get("title", "Unknown Title")); title.setWordWrap(True)
        f_title = QFont(); f_title.setPointSize(24); title.setFont(f_title); text.addWidget(title)

        conf_color = get_confidence_color(self.match_val)
        match = QLabel(f'<font color="{conf_color}">Match: {self.match_val:.0%}</font>')
        f_match = QFont(); f_match.setPointSize(22); match.setFont(f_match); text.addWidget(match)

        rel = QLabel(f"Released: {self.candidate.get('released', 'Unknown')}"); f_rel = QFont(); f_rel.setPointSize(20); rel.setFont(f_rel); text.addWidget(rel)

        devs = self.candidate.get("developers", [])
        producer = devs[0].get("name", "Unknown") if devs and isinstance(devs[0], dict) else (devs[0] if devs else "Unknown")
        prod = QLabel(f"Producer: {producer}"); f_prod = QFont(); f_prod.setPointSize(20); prod.setFont(f_prod); text.addWidget(prod)

        rating = self.candidate.get("rating", None)
        rate = QLabel(f"Rating: {rating}" if rating is not None else "Rating: N/A"); f_rate = QFont(); f_rate.setPointSize(20); rate.setFont(f_rate); text.addWidget(rate)

        info.addLayout(text)
        outer.addLayout(info)

        # Relations table
        relations = self.candidate.get("relations", [])
        official = [r for r in relations if r.get("relation_official") is True]
        if official:
            group = QGroupBox("Related Visual Novels")
            grid = QGridLayout()
            grid.setHorizontalSpacing(20); grid.setVerticalSpacing(10)
            grid.setColumnStretch(0, 0); grid.setColumnStretch(1, 0); grid.setColumnStretch(2, 2); grid.setColumnStretch(3, 1)
            hdrs = ["Image", "Relation", "Title", "Producer"]
            for c, text_hdr in enumerate(hdrs):
                h = QLabel(text_hdr); f = h.font(); f.setBold(True); h.setFont(f)
                grid.addWidget(h, 0, c, alignment=Qt.AlignmentFlag.AlignLeft)
            row = 1
            for rel in official:
                img = QLabel()
                rimg = rel.get("image", {})
                rid = rel.get("id")
                url = rimg.get("url") if isinstance(rimg, dict) else None
                if url:
                    try:
                        r = requests.get(url, stream=True, timeout=10)
                        r.raise_for_status()
                        pm = QPixmap()
                        pm.loadFromData(r.content)
                        pm = pm.scaled(100, 100, Qt.AspectRatioMode.KeepAspectRatio,
                                       Qt.TransformationMode.SmoothTransformation)
                        apply_censor_to_label(
                            img, pm,
                            enabled=getattr(self, "censor_enabled", True),
                            mode=normalize_censor_mode(getattr(self, "censor_mode", "blur")),
                            is_adult=is_adult_relation(rel)
                        )
                    except Exception as e:
                        print("Relation image error:", e)
                grid.addWidget(img, row, 0)
                grid.addWidget(QLabel(relation_map.get(rel.get("relation", "Unknown"), rel.get("relation", "Unknown"))), row, 1)
                grid.addWidget(QLabel(rel.get("title", "Unknown")), row, 2)
                rdevs = rel.get("developers", [])
                rprod = rdevs[0].get("name", "Unknown") if rdevs and isinstance(rdevs[0], dict) else (rdevs[0] if rdevs else "Unknown")
                grid.addWidget(QLabel(rprod), row, 3)
                row += 1
            group.setLayout(grid)
            outer.addWidget(group)
        else:
            outer.addWidget(QLabel("No official related visual novels found."))

class CandidateTile(QWidget):
    def __init__(self, candidate, parent=None):
        super().__init__(parent)
        self.candidate = candidate
        self.censor_enabled = True
        self.censor_mode = "pixelate"
        self.match_val = candidate.get("match", 0.0)
        main = QHBoxLayout(self); main.setContentsMargins(8, 8, 8, 8); main.setSpacing(12)

        # image
        self.image_label = QLabel()
        self.image_label.setFixedSize(150, 150)
        img_info = candidate.get("image", {})
        if isinstance(img_info, dict) and "url" in img_info:
            try:
                r = requests.get(img_info["url"], stream=True, timeout=10)
                r.raise_for_status()
                pm = QPixmap()
                pm.loadFromData(r.content)
                pm = pm.scaled(150, 150, Qt.AspectRatioMode.KeepAspectRatio,
                               Qt.TransformationMode.SmoothTransformation)
                apply_censor_to_label(
                    self.image_label,
                    pm,
                    enabled=getattr(parent, "censor_checkbox", None).isChecked() if parent and getattr(parent, "censor_checkbox", None) else True,
                    mode=normalize_censor_mode(getattr(parent, "censor_mode", None).currentText() if parent and getattr(parent, "censor_mode", None) else "Blur"),
                    is_adult=is_adult_candidate(candidate)
                )
            except Exception as e:
                print("Image error:", e)
        main.addWidget(self.image_label, alignment=Qt.AlignmentFlag.AlignTop)

        # text column
        text = QVBoxLayout(); text.setContentsMargins(0, 0, 0, 0); text.setSpacing(6)
        title = QLabel(candidate.get("title", "Unknown Title")); title.setWordWrap(True)
        f_title = QFont(); f_title.setPointSize(16); title.setFont(f_title)
        conf_color = get_confidence_color(self.match_val)
        match = QLabel(f'<font color="{conf_color}">Match: {self.match_val:.0%}</font>'); f_match = QFont(); f_match.setPointSize(18); match.setFont(f_match)
        rel = QLabel(f"Released: {candidate.get('released', 'Unknown')}"); f_rel = QFont(); f_rel.setPointSize(14); rel.setFont(f_rel)
        devs = candidate.get("developers", [])
        producer = devs[0].get("name", "Unknown") if devs and isinstance(devs[0], dict) else (devs[0] if devs else "Unknown")
        prod = QLabel(f"Producer: {producer}"); f_prod = QFont(); f_prod.setPointSize(14); prod.setFont(f_prod)
        text.addWidget(title); text.addWidget(match); text.addWidget(rel); text.addWidget(prod); text.addStretch(1)
        main.addLayout(text)
        self.setMinimumHeight(160)

    def sizeHint(self):
        return QSize(360, 160)

# -------------------- main window --------------------
class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("VN Folder Renamer")
        self.settings = QSettings("HiddenTiger", "VNFolderRenamer")
        last = self.settings.value("lastDirectory", "")
        self.directory = last if last and os.path.isdir(str(last)) else None

        main_splitter = QSplitter(Qt.Orientation.Horizontal, self)
        main_splitter.setHandleWidth(6)
        main_splitter.setStretchFactor(0, 4)
        #main_splitter.setStretchFactor(1, 1)
        self.setCentralWidget(main_splitter)

        # Left
        left = QWidget(); left_v = QVBoxLayout(left); left_v.setAlignment(Qt.AlignmentFlag.AlignTop)
        dir_row = QHBoxLayout()
        self.dir_label = QLabel("Selected Directory:")
        self.dir_edit = QLineEdit(""); self.dir_edit.editingFinished.connect(self.refresh_folder_list)
        dir_row.addWidget(self.dir_label); dir_row.addWidget(self.dir_edit)
        left_v.addLayout(dir_row)
        btn = QPushButton("Select Directory"); btn.setMaximumSize(150, 30); btn.clicked.connect(self.select_directory)
        left_v.addWidget(btn)
        left_v.addWidget(QLabel("Folders in Directory:"))
        self.folder_list = QListWidget(); self.folder_list.itemClicked.connect(self.load_folder_details)
        left_v.addWidget(self.folder_list)
        if self.directory:
            self.dir_edit.setText(self.directory); self.refresh_folder_list()
        main_splitter.addWidget(left)

        # Middle
        mid = QWidget(); mid_v = QVBoxLayout(mid); mid_v.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.original_label = QLabel("Original Folder Name:"); self.original_value = QLabel("")
        mid_v.addWidget(self.original_label); mid_v.addWidget(self.original_value)
        self.extracted_title_label = QLabel("Extracted Title for API Search:"); self.title_edit = QLineEdit("")
        mid_v.addWidget(self.extracted_title_label); mid_v.addWidget(self.title_edit)
        self.expected_info_label = QLabel("Expected Release Date and Producer:"); mid_v.addWidget(self.expected_info_label)
        search_row = QHBoxLayout()
        self.search_button = QPushButton("Search Candidates"); self.search_button.setMaximumSize(150, 30)
        self.search_button.clicked.connect(self.search_candidates); search_row.addWidget(self.search_button); search_row.addStretch()
        search_row.addWidget(QLabel("Number of Candidates:"))
        self.candidate_count_spin = QSpinBox(); self.candidate_count_spin.setRange(1, 20); self.candidate_count_spin.setValue(5)
        search_row.addWidget(self.candidate_count_spin)
        mid_v.addLayout(search_row)
        self.candidate_list = QListWidget()
        self.candidate_list.itemClicked.connect(self.select_candidate)
        self.candidate_list.itemDoubleClicked.connect(self.open_candidate_detail)
        mid_v.addWidget(QLabel("Candidates:"))
        mid_v.addWidget(self.candidate_list)
        main_splitter.addWidget(mid)

        # Right
        right = QWidget(); right_v = QVBoxLayout(right); right_v.setAlignment(Qt.AlignmentFlag.AlignTop)
        # Label above field + larger fonts
        self.suggested_label = QLabel("Suggested New Folder Name:")
        self.suggested_label.setMinimumWidth(80)
        f_lbl = QFont(); f_lbl.setPointSize(13); f_lbl.setBold(True); self.suggested_label.setFont(f_lbl)
        self.suggested_name_edit = QLineEdit(); self.suggested_name_edit.setClearButtonEnabled(True)
        f_edit = self.font(); f_edit.setPointSize(11); self.suggested_name_edit.setFont(f_edit)
        self.suggested_name_edit.setMinimumHeight(30)
        self.suggested_name_edit.setMaximumWidth(80)
        right_v.addWidget(self.suggested_label)
        right_v.addWidget(self.suggested_name_edit)

        self.rename_button = QPushButton("Rename Folder"); self.rename_button.setMaximumSize(150, 30)
        self.rename_button.clicked.connect(self.rename_folder); right_v.addWidget(self.rename_button)

        options_group = QGroupBox("Options"); og_v = QVBoxLayout(options_group)
        options_group.setMinimumWidth(30)
        toggles = QHBoxLayout()
        self.use_jp_checkbox = QCheckBox("Use Japanese (original) Title")
        self.use_jp_checkbox.toggled.connect(self.update_suggested_name)
        toggles.addWidget(self.use_jp_checkbox)
        self.shortcut_checkbox = QCheckBox("Generate VNDB Shortcut (.url)"); toggles.addWidget(self.shortcut_checkbox)
        og_v.addLayout(toggles)

        self.censor_checkbox = QCheckBox("Censor 18+ images")
        self.censor_checkbox.setChecked(True)
        toggles.addWidget(self.censor_checkbox)

        self.censor_mode = QComboBox()
        self.censor_mode.addItems(["Blur", "Cover"])
        toggles.addWidget(self.censor_mode)

        # When toggled/changed, re-render the tiles in place
        self.censor_checkbox.toggled.connect(self.refresh_candidate_tiles)
        self.censor_mode.currentIndexChanged.connect(self.refresh_candidate_tiles)

        self.flag_checkboxes = {}
        flags_layout = QHBoxLayout()
        for symbol, desc in {"★": "Fandisc Confirmed", "☆": "Fandisc Expected", "♫": "OST Included"}.items():
            cb = QCheckBox(f"{symbol}\n({desc})"); cb.toggled.connect(self.update_suggested_name)
            self.flag_checkboxes[symbol] = cb; flags_layout.addWidget(cb)
        self.flags_panel = CollapsiblePanel("Optional Flags"); self.flags_panel.setContentLayout(flags_layout); og_v.addWidget(self.flags_panel)

        tags_layout = QFormLayout()
        self.tags_edit = QLineEdit(""); self.tags_edit.setPlaceholderText("e.g., Romance, Slice of Life")
        self.tags_edit.textChanged.connect(self.update_suggested_name)
        tags_layout.addRow("Optional API Tags:", self.tags_edit)
        self.tags_panel = CollapsiblePanel("Optional Tags"); self.tags_panel.setContentLayout(tags_layout); og_v.addWidget(self.tags_panel)

        self.template_editor = CustomTemplateEditor()
        self.template_editor.templateChanged.connect(self.update_suggested_name)
        ed_box = QVBoxLayout()
        ed_box.addWidget(QLabel("Custom Naming Template (Drag & drop, toggle items):"))
        ed_box.addWidget(self.template_editor)
        row = QHBoxLayout(); row.addWidget(QLabel("Template Preview:"))
        self.template_preview = QLabel(self.template_editor.getTemplate()); self.template_preview.setWordWrap(True)
        row.addWidget(self.template_preview); ed_box.addLayout(row)
        self.template_panel = CollapsiblePanel("Custom Naming Template"); self.template_panel.setContentLayout(ed_box); og_v.addWidget(self.template_panel)

        options_group.setLayout(og_v)
        right_v.addWidget(options_group)
        main_splitter.addWidget(right)

        # IMPORTANT: give THREE sizes (left/mid/right) so right pane is visible
        main_splitter.setSizes([360, 620, 420])

    # ---------- actions ----------
    def select_directory(self):
        directory = QFileDialog.getExistingDirectory(self, "Select Folder Directory")
        if directory:
            self.directory = directory
            self.dir_edit.setText(directory)
            self.refresh_folder_list()
            self.settings.setValue("lastDirectory", directory)

    def refresh_folder_list(self):
        if not self.directory:
            return
        self.folder_list.clear()
        try:
            folders = [f for f in os.listdir(self.directory) if os.path.isdir(os.path.join(self.directory, f))]
            self.folder_list.addItems(sorted(folders))
        except Exception as e:
            QMessageBox.warning(self, "Error", f"Error reading directory: {e}")

    def load_folder_details(self, item: QListWidgetItem):
        try:
            folder_name = item.text()
            self.current_folder_name = folder_name
            self.current_folder_path = os.path.join(self.directory, folder_name)
            self.original_value.setText(folder_name)
            parsed = parse_folder_name(folder_name)
            self.title_edit.setText(parsed.get("title", folder_name))
            expected_date, expected_producer = parse_bracket_info(folder_name)
            self.expected_info_label.setText(f"Expected Release Date: {expected_date} | Expected Producer: {expected_producer}")
            self.candidate_list.clear()
            self.suggested_name_edit.clear()
        except Exception as e:
            QMessageBox.warning(self, "Error", f"Error loading folder details: {e}")

    def search_candidates(self):
        query = self.title_edit.text().strip()
        if not query:
            QMessageBox.warning(self, "Error", "No search query provided.")
            return
        expected_date, expected_producer = parse_bracket_info(self.current_folder_name)
        limit = self.candidate_count_spin.value()
        candidates = get_vn_candidates(query, expected_date, expected_producer, limit)
        if not candidates and " " in query:
            new_query = query.split(" ", 1)[0].strip()
            QMessageBox.information(self, "Search Query",
                                    f"No candidates found for '{query}'.\nTrying shortened query: '{new_query}'")
            candidates = get_vn_candidates(new_query, expected_date, expected_producer, limit)
        self.candidate_list.clear()
        if not candidates:
            QMessageBox.information(self, "Candidates", "No VN candidates found.")
            return
        for cand in candidates:
            item = QListWidgetItem()
            tile = CandidateTile(cand, parent=self)
            item.setSizeHint(tile.sizeHint())
            item.setData(Qt.ItemDataRole.UserRole, cand)
            self.candidate_list.addItem(item)
            self.candidate_list.setItemWidget(item, tile)

    def update_suggested_name(self):
        if not hasattr(self, "vn_info") or self.vn_info is None:
            return
        title_override = None
        if self.use_jp_checkbox.isChecked():
            jp = None
            for t in self.vn_info.get("titles", []):
                if t.get("main"):
                    jp = t.get("title"); break
            if not jp and self.vn_info.get("titles"):
                jp = self.vn_info["titles"][0].get("title")
            title_override = jp or self.vn_info.get("title", "")
        flags = [s for s, cb in self.flag_checkboxes.items() if cb.isChecked()]
        custom_template = self.template_editor.getTemplate()
        new_name = suggest_new_folder_name(
            self.vn_info, getattr(self, "release_info", None),
            optional_flags=", ".join(flags),
            optional_tags=self.tags_edit.text().strip(),
            custom_template=custom_template,
            title_override=title_override
        )
        self.suggested_name_edit.setText(new_name)
        self.template_preview.setText(custom_template)

    def select_candidate(self, item: QListWidgetItem):
        try:
            cand = item.data(Qt.ItemDataRole.UserRole)
            if not cand or not isinstance(cand, dict):
                return
            self.vn_info = cand
            vn_id = cand.get("id", "")
            if vn_id and not str(vn_id).startswith("v"):
                vn_id = "v" + str(vn_id)
            self.vn_info["id"] = vn_id
            # Try release lookup; if it fails, just keep None (no popup)
            self.release_info = search_release(vn_id)
            self.update_suggested_name()
        except Exception as e:
            QMessageBox.warning(self, "Error", f"Error selecting candidate: {e}")

    def open_candidate_detail(self, item: QListWidgetItem):
        cand = item.data(Qt.ItemDataRole.UserRole)
        if not isinstance(cand, dict):
            return

        # Works even if these widgets aren’t present yet
        censor_enabled = bool(getattr(self, "censor_checkbox", None) and self.censor_checkbox.isChecked())
        mode = normalize_censor_mode(self.censor_mode.currentText()) if getattr(self, "censor_mode", None) else "blur"

        dlg = CandidateDetailDialog(
            cand,
            censor_enabled=self.censor_checkbox.isChecked() if hasattr(self, "censor_checkbox") else True,
            censor_mode=mode,
            parent=self
        )
        dlg.exec()

    def refresh_candidate_tiles(self):
        """Rebuild tile widgets to apply/clear censoring without new API calls."""
        mode = normalize_censor_mode(self.censor_mode.currentText())
        for i in range(self.candidate_list.count()):
            item = self.candidate_list.item(i)
            cand = item.data(Qt.ItemDataRole.UserRole)
            tile = CandidateTile(cand, parent=self)
            item.setSizeHint(tile.sizeHint())
            self.candidate_list.setItemWidget(item, tile)

    def rename_folder(self):
        new_name = self.suggested_name_edit.text().strip()
        if not new_name:
            QMessageBox.warning(self, "Error", "No new folder name suggested.")
            return
        reply = QMessageBox.question(
            self, "Confirm Rename",
            f"Do you want to rename the folder:\n{self.current_folder_name}\n\nto\n\n{new_name}?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        old_path = os.path.join(self.directory, self.current_folder_name)
        new_path = os.path.join(self.directory, new_name)
        try:
            os.rename(old_path, new_path)
            QMessageBox.information(self, "Success", f"Folder renamed to:\n{new_name}")
            self.refresh_folder_list()
            if self.shortcut_checkbox.isChecked():
                create_vndb_shortcut(new_path, self.vn_info["id"])
        except Exception as e:
            QMessageBox.warning(self, "Error", f"Error renaming folder: {e}")

# -------------------- app --------------------
class MainWindowApp(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("VN Folder Renamer")
        self.resize(1200, 800)
        self.setCentralWidget(MainWindow())

if __name__ == "__main__":
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    f = app.font()
    f.setPointSize(f.pointSize() + 1)
    app.setFont(f)
    window = MainWindowApp()
    window.show()
    sys.exit(app.exec())
