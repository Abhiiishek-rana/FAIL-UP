import sys
import os
import re
import subprocess
from datetime import datetime
from playwright.sync_api import sync_playwright

# PySide6 Core
from PySide6.QtCore import (
    Qt, QSize, QTimer, QPropertyAnimation, QEasingCurve, Signal, QUrl, QObject, QThread, QRectF,
    QSettings
)

# PySide6 GUI
from PySide6.QtGui import (
    QIcon, QColor, QLinearGradient, QPalette, QBrush, QFont, QPainter, QTextCursor, QPixmap,
    QPainterPath, QFontMetrics, QAction
)

# PySide6 Widgets
from PySide6.QtWidgets import (
    QApplication, QWidget, QDialog, QLabel, QPushButton, QLineEdit, QListWidget, QListWidgetItem,
    QVBoxLayout, QHBoxLayout, QGridLayout, QStackedLayout, QScrollArea,
    QTextEdit, QTextBrowser, QSplitter, QFrame, QGraphicsDropShadowEffect,
    QGroupBox, QRadioButton, QComboBox, QButtonGroup, QStyledItemDelegate, QStyle,
    QMenu, QMessageBox
)

# PySide6 Web
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWebEngineCore import QWebEngineSettings
from PySide6.QtSvgWidgets import QSvgWidget
from PySide6.QtWebEngineCore import QWebEngineProfile, QWebEnginePage
# External Libraries
from youtube_transcript_api import YouTubeTranscriptApi, TranscriptsDisabled, NoTranscriptFound
import ollama
import markdown
from bs4 import BeautifulSoup
from openai import OpenAI


BASE_DIR = os.path.dirname(os.path.abspath(__file__))
ICON = lambda filename: os.path.join(BASE_DIR, "icons", filename)

class TranscriptWorker(QObject):
    finished = Signal(str)
    error = Signal(str)

    def __init__(self, video_id):
        super().__init__()
        self.video_id = video_id
        self.max_retries = 10

    def fetch_transcript(self):
        attempt = 0
        while attempt < self.max_retries:
            try:
                if not os.path.exists("transcript"):
                    os.makedirs("transcript")

                transcript = None

               
                try:
                    transcript = YouTubeTranscriptApi.get_transcript(self.video_id, languages=['en'])
                except NoTranscriptFound:
           
                    transcript_list = YouTubeTranscriptApi.list_transcripts(self.video_id)

             
                    for t in transcript_list:
                        if t.language_code == 'en' and t.is_generated:
                            transcript = t.fetch()
                            break

                    if transcript is None:
                        for t in transcript_list:
                            if t.is_translatable:
                                try:
                                    transcript = t.translate('en').fetch()
                                    break
                                except Exception:
                                    continue

                    if transcript is None:
                        raise NoTranscriptFound("No suitable transcript found.")

                filename = "transcript/transcript.txt"
                with open(filename, "w", encoding="utf-8") as f:
                    for entry in transcript:
                        text = entry['text'] if isinstance(entry, dict) else entry.text
                        f.write(f"{text}\n")

                self.finished.emit(filename)
                return

            except Exception as e:
                attempt += 1

        self.error.emit("Failed to fetch transcript after multiple attempts.")


class NotesGenerationWorker(QObject):
    chunk_received = Signal(str)
    finished = Signal(str)
    error = Signal(str)

    def __init__(self, transcript, llm_type="local", model="qwen3:4b", api_key=None):
        super().__init__()
        self.transcript = transcript
        self.llm_type = llm_type
        self.model = model
        self.api_key = api_key

    def generate_notes(self):
        try:
            system_prompt = """
You are an expert at transforming YouTube video transcripts into deeply detailed, logically structured, and fully self-contained notes that replicate the depth and value of the original content.

🎯 OBJECTIVE:
Your task is to generate **expert-level notes** from a **YouTube video transcript**. The resulting notes must serve as a **complete replacement** for watching the video — comprehensive, in-depth, and structured for clarity and accessibility.

📥 INPUT:
A raw transcript of a YouTube video. This may include narration, dialogues, visual references, explanations, examples, definitions, and topic transitions.

📝 OUTPUT REQUIREMENTS:
Produce notes in the form of a **fully structured HTML document**, using semantic and readable HTML tags. The notes must:

- Be **clear, coherent, and in-depth** — not mere summaries or paraphrases.
- Fully reflect the speaker's intent, knowledge, and structure.
- Cover **every major idea, example, explanation, and insight** in a refined and readable format.

### ✅ INCLUDE:
- **Key ideas and core concepts** clearly defined and explained.
- **Section-wise summaries** following the natural flow of the video.
- **Relevant examples, case studies, or stories** from the content.
- **Significant insights or quotes**, rephrased if needed, and optionally placed in `<blockquote>` tags.
- **Definitions and clarifications** of technical or domain-specific terms using `<p>`, lists, or `<table>` where appropriate.
- **Contextual use of HTML tags**, such as:

### 🔹 HTML FORMAT GUIDE:
Use these tags for structure and clarity:
- `<h1>`: Main title (video topic or title)
- `<h2>`, `<h3>`, `<h4>`: Section and subsection headers
- `<p>`: Paragraphs
- `<strong>`: Bold for key terms or emphasis
- `<em>`: Italics for nuance or subtle emphasis
- `<u>`: Underlined (use sparingly)
- `<blockquote>`: For highlighting significant paraphrased statements or quotes
- `<ul>` / `<ol>` / `<li>`: Bullet points and ordered steps
- `<code>`: Inline code or technical terms
- `<pre><code>`: Full code blocks
- `<table>`: Structured information such as definitions, comparisons, lists, pros/cons
- `<hr>`: Optional horizontal dividers for major breaks
- `<a href="...">`: Links, if referenced in transcript
- `<figure>` and `<figcaption>`: If visuals are described or referenced

🧾 STYLE & CONTENT GUIDELINES:
- Maintain a **neutral, informative tone** throughout.
- Avoid raw dialogue or casual speech—transform into polished, educational writing.
- Eliminate all **filler language**, off-topic digressions, or promotional content unless reframed to add meaningful value.
- Use **headings and lists** to organize content into **readable, skimmable sections**.
- **Use detailed explanations** — short where possible, longer where needed.
- **Use semantic structure** to enhance clarity and comprehension.

🌐 LANGUAGE:
Respond **in the same language as the transcript**. Do not translate unless explicitly instructed.

📤 OUTPUT:
Respond with only the **final HTML content** — no comments, markdown, or explanations.

---

✅ FINAL REVIEW CHECKLIST:
Before submitting the output, ensure the following:

- **Completeness**: All key insights and supporting ideas are included.
- **Clarity**: Each concept is fully explained, with proper flow.
- **Structure**: HTML is well-organized with appropriate tags for headings, lists, emphasis, and sections.
- **Faithfulness**: The meaning and intent of the original content are preserved, not just paraphrased.
- **Polish**: No raw transcript content. No typos. No repetition or duplication.

❌ DO NOT:
- ❌ Copy or reuse raw transcript lines or filler dialogue.
- ❌ Use oversimplified summaries or vague list items.
- ❌ Repeat content or duplicate section headers.
- ❌ Force headings like "Introduction" unless explicitly mentioned.
- ❌ Include irrelevant, casual, or promotional speech unless transformed into educational context.
- ❌ Misspell or misrepresent any technical terms.
"""

            messages = [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": f"Here is the transcript:\n\n{self.transcript}"}
            ]

            if self.llm_type == "local":
                # Use Ollama locally
                response = ollama.chat(model=self.model, messages=messages, stream=True)
                full_text = ""
                for chunk in response:
                    content = chunk.get('message', {}).get('content', '')
                    if content:
                        full_text += content
                        self.chunk_received.emit(content)
                self.finished.emit("")
            else:
                # Use OpenRouter
                client = OpenAI(
                    base_url="https://openrouter.ai/api/v1",
                    api_key=self.api_key,
                )

                response = client.chat.completions.create(
                    model=self.model,
                    messages=messages,
                    stream=True,
                )

                full_text = ""
                for chunk in response:
                    content = chunk.choices[0].delta.content or ""
                    if content:
                        full_text += content
                        self.chunk_received.emit(content)
                self.finished.emit("")

        except Exception as e:
            self.error.emit(str(e))

class PDFListDelegate(QStyledItemDelegate):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.parent_widget = parent  
    def sizeHint(self, option, index):
        text = index.data(Qt.DisplayRole)
        font = option.font
        fm = QFontMetrics(font)
        margin = 15
        width = self.parent_widget.width() - 2 * margin 
        rect = fm.boundingRect(0, 0, width, 0, Qt.TextWordWrap, text)
        return QSize(width, rect.height() + 2 * margin)

    def paint(self, painter, option, index):
        painter.save()
        
        if option.state & QStyle.State_Selected:
            painter.fillRect(option.rect, QColor("#7c4dff"))
            painter.setPen(QColor("white"))
        elif option.state & QStyle.State_MouseOver:
            painter.fillRect(option.rect, QColor("#2a1e42"))
            painter.setPen(QColor("#e0e0e0"))
        else:
            painter.setPen(QColor("#e0e0e0"))
        
        text = index.data(Qt.DisplayRole)
        margin = 15
        text_rect = option.rect.adjusted(margin, margin, -margin, -margin)
        painter.drawText(text_rect, Qt.TextWordWrap, text)
        
        painter.restore()

    def createEditor(self, parent, option, index):
        editor = QLineEdit(parent)
        editor.setStyleSheet("""
            QLineEdit {
                background-color: #2a1e42;
                color: #e0e0e0;
                border: 1px solid #7c4dff;
                border-radius: 4px;
                padding: 5px;
            }
        """)
        return editor

    def setEditorData(self, editor, index):
        editor.setText(index.data(Qt.DisplayRole))

    def setModelData(self, editor, model, index):
        new_name = editor.text()
        if new_name and new_name != index.data(Qt.DisplayRole):
            pdf_path = index.data(Qt.UserRole)
            new_path = os.path.join(os.path.dirname(pdf_path), new_name)
            try:
                os.rename(pdf_path, new_path)
                model.setData(index, new_name, Qt.DisplayRole)
                model.setData(index, new_path, Qt.UserRole)
                if self.parent_widget:
                    self.parent_widget.show_notification(f"Renamed to: {new_name}")
            except Exception as e:
                if self.parent_widget:
                    self.parent_widget.show_notification(f"Error renaming: {str(e)}")


class YouTubeNotesView(QWidget):
    def __init__(self, web_view, transcript_file, llm_type="local", model="qwen3:4b", api_key=None):
        super().__init__()
        self.web_view = web_view
        self.transcript_file = transcript_file
        self.current_markdown = ""
        self.llm_type = llm_type
        self.model = model
        self.api_key = api_key
        self.parent_window = None
        
        self.pending_think_content = ""
        self.in_think_block = False

        self.init_ui()
        self.load_transcript()
        self.start_notes_generation()

    

    def init_ui(self):
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        self.splitter = QSplitter(Qt.Horizontal)

        video_container = QWidget()
        video_layout = QVBoxLayout(video_container)
        video_layout.setContentsMargins(0, 0, 0, 0)
        video_layout.addWidget(self.web_view)

    

        notes_container = QWidget()
        notes_container.setStyleSheet("background-color: #1f1a30;")
        notes_layout = QVBoxLayout(notes_container)
        notes_layout.setContentsMargins(10, 10, 10, 10)

        notes_header = QLabel("Video Notes")
        notes_header.setStyleSheet("""
            QLabel {
                color: #b388ff;
                font-size: 18px;
                font-weight: bold;
                padding-bottom: 10px;
                border-bottom: 1px solid #3a0841;
                margin-left: 0;
            }
        """)
        notes_layout.addWidget(notes_header)

        self.notes_panel = QTextBrowser()
        self.notes_panel.setStyleSheet("""
            QTextBrowser {
                background-color: #1a1426;
                color: #e0e0e0;
                border: 1px solid #3a0841;
                border-radius: 5px;
                padding: 5px;
                font-size: 13px;
                margin: 0;
            }
            a { 
                color: #7c4dff; 
                text-decoration: none; 
            }
            h1, h2, h3, h4 { 
                color: #b388ff; 
                margin-top: 12px; 
                margin-bottom: 8px;
                margin-left: 0;
                padding-left: 0;
            }
            p {
                margin-left: 0;
                padding-left: 0;
            }
            code {
                background-color: #2a1e42;
                padding: 2px 4px;
                border-radius: 3px;
                font-family: monospace;
                margin-left: 0;
            }
            pre {
                background-color: #2a1e42;
                padding: 8px;
                border-radius: 4px;
                white-space: pre-wrap;
                margin-left: 0;
            }
            blockquote {
                border-left: 3px solid #7c4dff;
                padding-left: 10px;
                margin-left: 0;
                color: #a0a0a0;
            }
            ul, ol {
                margin-top: 5px;
                margin-bottom: 5px;
                padding-left: 20px;
                margin-left: 0;
            }
            li { 
                margin-bottom: 3px;
                margin-left: 0;
            }
        """)
        self.notes_panel.setOpenExternalLinks(True)
        notes_layout.addWidget(self.notes_panel, 1)

        self.continue_button = QPushButton("Generate PDF")
        self.continue_button.setFixedHeight(36)
        self.continue_button.setStyleSheet("""
            QPushButton {
                background-color: #2a1e42;
                color: #00ff88;
                border: none;
                border-radius: 8px;
                font-size: 13px;
            }
            QPushButton:hover {
                background-color: #3a2a5a;
            }
            QPushButton:disabled {
                color: #666666;
            }
        """)
        self.continue_button.clicked.connect(self.on_continue_clicked)
        notes_layout.addWidget(self.continue_button)

        self.splitter.addWidget(video_container)
        self.splitter.addWidget(notes_container)
        self.splitter.setHandleWidth(5)
        self.splitter.setStyleSheet("""
            QSplitter::handle {
                background-color: #3a0841;
            }
            QSplitter::handle:hover {
                background-color: #7c4dff;
            }
        """)
        main_layout.addWidget(self.splitter)
        self.splitter.setSizes([self.width() // 2, self.width() // 2])

    def load_transcript(self):
        try:
            with open(self.transcript_file, "r", encoding="utf-8") as f:
                self.transcript = f.read()
        except Exception as e:
            self.notes_panel.setPlainText(f"Error loading transcript: {str(e)}")

    def get_loading_indicator(self):
        return """
        <div style="text-align: center; padding: 20px;">
            <div style="margin-bottom: 15px; color: #b388ff;">Generating notes...</div>
            <div style="width: 50px; height: 50px; margin: 0 auto;
                border: 5px solid #3a0841;
                border-top: 5px solid #7c4dff;
                border-radius: 50%;
                animation: spin 1s linear infinite;"></div>
            <style>
                @keyframes spin {
                    0% { transform: rotate(0deg); }
                    100% { transform: rotate(360deg); }
                }
            </style>
        </div>
        """

    def process_text_chunk(self, text):
        result = ""
        remaining = text
        while remaining:
            if not self.in_think_block:
                think_start = remaining.find('<think>')
                if think_start >= 0:
                    result += remaining[:think_start]
                    remaining = remaining[think_start + len('<think>'):]
                    self.in_think_block = True
                else:
                    result += remaining
                    break
            else:
                think_end = remaining.find('</think>')
                if think_end >= 0:
                    remaining = remaining[think_end + len('</think>'):]
                    self.in_think_block = False
                else:
                    self.pending_think_content += remaining
                    remaining = ""
        return result

    def append_to_notes_panel(self, text):
        if self.pending_think_content:
            text = self.pending_think_content + text
            self.pending_think_content = ""
        
        clean_text = self.process_text_chunk(text)
        if clean_text and not self.in_think_block:
            self.current_markdown += clean_text
            html = self.markdown_to_html(self.current_markdown, for_pdf=False)
            if self.in_think_block:
                html += self.get_loading_indicator()
            self.notes_panel.setHtml(html)
            self.notes_panel.moveCursor(QTextCursor.End)

    def markdown_to_html(self, markdown_text, for_pdf=False):
        clean_text = re.sub(r'<style.*?>.*?</style>', '', markdown_text, flags=re.DOTALL)
        clean_text = re.sub(r'style="[^"]*"', '', clean_text)
        clean_text = re.sub(r'```html?', '', clean_text)
        clean_text = re.sub(r'```', '', clean_text)
        html = markdown.markdown(clean_text)
        soup = BeautifulSoup(html, 'html.parser')
        for pre in soup.find_all('pre'):
            if not pre.code:
                pre.wrap(soup.new_tag('code'))\
        
        for a in soup.find_all('a', href=True):
            a['target'] = '_blank'
            a['rel'] = 'noopener noreferrer'


        style = soup.new_tag('style')
        style.string = """
        body {
            color: #000000;
            font-family: 'Georgia', 'Times New Roman', serif;
            font-size: 12pt;
            line-height: 1.6;
            margin: 0;
            padding: 0;
        }
        h1, h2, h3, h4 {
            color: #000000;
            font-weight: bold;
            margin-top: 1.2em;
            margin-bottom: 0.5em;
        }
        p {
            margin: 0.75em 0;
        }
        ul, ol {
            margin: 0.75em 0 0.75em 2em;
            padding-left: 1em;
        }
        li {
            margin-bottom: 0.25em;
        }
        blockquote {
            border-left: 3px solid #888;
            padding-left: 10px;
            margin-left: 0;
            color: #444;
            font-style: italic;
        }
        code {
            font-family: 'Courier New', monospace;
            background-color: #f0f0f0;
            padding: 2px 4px;
            border-radius: 4px;
        }
        pre {
            font-family: 'Courier New', monospace;
            background-color: #f0f0f0;
            padding: 10px;
            border-radius: 4px;
            white-space: pre-wrap;
        }
        table {
            width: 100%;
            border-collapse: collapse;
            margin: 1em 0;
        }
        th, td {
            border: 1px solid #ccc;
            padding: 8px;
            text-align: left;
        }
        a {
            color: #1a0dab;
            text-decoration: none;
        }
        a:hover {
            text-decoration: underline;
        }
    """
        if for_pdf:
            style.string += """
        body {
            color: #000000;
            font-size: 12pt;
        }
        h1, h2, h3, h4 {
            color: #000000;
        }
        blockquote {
            border-color: #888;
            color: #444;
        }
        code, pre {
            background-color: #f0f0f0;
            color: #000000;
        }
        a {
            color: #1a0dab;
        }
        """
        else:
            style.string += """
        body {
            color: #e0e0e0;
            background-color: #1a1426;
        }
        h1, h2, h3, h4 {
            color: #b388ff;
        }
        a {
            color: #7c4dff;
        }
        """
        if soup.head:
            soup.head.insert(0, style)
        elif soup.html:
            soup.html.insert(0, style)
        else:
            soup.insert(0, style)

        return str(soup)


    def start_notes_generation(self):
        if not hasattr(self, 'transcript'):
            return
        
        self.notes_panel.setHtml(self.get_loading_indicator())
        self.current_markdown = ""
        self.pending_think_content = ""
        self.in_think_block = False
        self.continue_button.setEnabled(False)
        QApplication.processEvents()

        self.notes_thread = QThread()
        self.notes_worker = NotesGenerationWorker(
            self.transcript,
            llm_type=self.llm_type,
            model=self.model,
            api_key=self.api_key
        )
        self.notes_worker.moveToThread(self.notes_thread)

        self.notes_worker.chunk_received.connect(self.append_to_notes_panel)
        self.notes_worker.finished.connect(self.on_notes_generated)
        self.notes_worker.error.connect(self.on_notes_error)
        self.notes_thread.started.connect(self.notes_worker.generate_notes)
        self.notes_thread.finished.connect(self.notes_thread.deleteLater)

        self.notes_thread.start()

    def on_notes_generated(self, notes):
        self.continue_button.setEnabled(True)
        self.notes_thread.quit()
        self.notes_thread.wait()

    def on_notes_error(self, error_msg):
        self.notes_panel.setHtml(f"""
        <div style='color: #ff6b6b; padding: 10px; border-left: 3px solid #ff6b6b; margin-left: 0;'>
            Error generating notes: {error_msg}
        </div>
        """)
        self.continue_button.setEnabled(True)
        self.notes_thread.quit()
        self.notes_thread.wait()

    def on_continue_clicked(self):
        try:                                                           
            html_content = self.markdown_to_html(self.current_markdown, for_pdf=True)
            soup = BeautifulSoup(html_content, 'html.parser')
            main_heading = "notes"
            h1 = soup.find('h1')
            if h1:
                main_heading = h1.get_text().strip()
                main_heading = re.sub(r'[^\w\-_\. ]', '', main_heading)
                if len(main_heading) > 50:
                    main_heading = main_heading[:50]
            
            if not os.path.exists("output"):
                os.makedirs("output")
            
            filename = f"output/{main_heading}.pdf" 
            
            with sync_playwright() as p:
                browser = p.chromium.launch()
                page = browser.new_page() 
                page.set_content(html_content) 
                page.pdf(
                    path=filename,
                    print_background=True,
                    format='A4',
                    margin={
                        'top': '15mm',
                        'right': '15mm',
                        'bottom': '15mm',
                        'left': '15mm'
                    },
                    display_header_footer=True,
                    header_template='<div style="height: 0;"></div>',
                    footer_template='<div style="font-size: 10px; width: 100%; text-align: center;"><span class="pageNumber"></span></div>',
                    prefer_css_page_size=True
                )
                browser.close()
            
            if self.parent_window:
                self.parent_window.load_pdf_list()
                self.parent_window.stacked_layout.setCurrentWidget(self.parent_window.pdf_list_view)
                
                for i in range(self.parent_window.pdf_list.count()):
                    item = self.parent_window.pdf_list.item(i)
                    if os.path.basename(item.data(Qt.UserRole)) == os.path.basename(filename):
                        self.parent_window.pdf_list.setCurrentItem(item)
                        self.parent_window.open_pdf(item)
                        break
                
                self.parent_window.show_notification(f"PDF saved as: {filename}")
            
        except Exception as e:
            self.show_notification(f"Error generating PDF: {str(e)}")

    def show_notification(self, message):
        notification = QLabel(message, self)
        notification.setStyleSheet("""
            QLabel {
                background-color: rgba(0, 255, 136, 0.2);
                color: #00ff88;
                border: 1px solid #00ff88;
                border-radius: 10px;
                padding: 10px 20px;
                font-size: 14px;
            }
        """)
        notification.adjustSize()
        notification.move((self.width() - notification.width()) // 2, 
                         self.height() - notification.height() - 20)
        notification.show()
        
        QTimer.singleShot(3000, notification.hide)

    def resizeEvent(self, event):
        self.splitter.setSizes([self.width() // 2, self.width() // 2])
        super().resizeEvent(event)

class SplashScreen(QDialog):
    finished = Signal()

    def __init__(self):
        super().__init__()
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint)
        self.setAttribute(Qt.WA_TranslucentBackground)

        splash_width = 500
        splash_height = 220
        self.setFixedSize(splash_width, splash_height)

        screen = QApplication.primaryScreen().geometry()
        x = (screen.width() - splash_width) // 2
        y = (screen.height() - splash_height) // 2
        self.move(x, y)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(30, 30, 30, 30)
        layout.setSpacing(25)
        layout.setAlignment(Qt.AlignCenter)

        self.github_logo = QSvgWidget(ICON("github.svg"))
        self.github_logo.setFixedSize(110, 110)
        layout.addWidget(self.github_logo)

        self.username_label = QLabel("Abhiiishek-rana")
        self.username_label.setFont(QFont("Segoe UI", 28, QFont.Bold))
        self.username_label.setStyleSheet("""
            color: qlineargradient(
                spread:pad,
                x1:0, y1:0,
                x2:1, y2:0,
                stop:0 #76c7ff,
                stop:1 #a29bfe
            );
        """)
        layout.addWidget(self.username_label)

        self.setWindowOpacity(1)
        QTimer.singleShot(4000, self.fade_out)

    def fade_out(self):
        self.animation = QPropertyAnimation(self, b"windowOpacity")
        self.animation.setDuration(800)
        self.animation.setStartValue(1)
        self.animation.setEndValue(0)
        self.animation.setEasingCurve(QEasingCurve.InOutQuad)
        self.animation.finished.connect(self.on_finished)
        self.animation.start()

    def on_finished(self):
        self.close()
        self.finished.emit()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)

        brush = QBrush(QColor("black"))
        rect = QRectF(0, 0, self.width(), self.height())
        painter.setBrush(brush)
        painter.setPen(Qt.NoPen)
        painter.drawRoundedRect(rect, 20, 20)

class ShadowButton(QPushButton):
    def __init__(self, icon_path):
        super().__init__()
        self.setFixedSize(180, 180)
        self.setIcon(QIcon(icon_path))
        self.setIconSize(QSize(96, 96))
        self.setCursor(Qt.PointingHandCursor)

        self.setStyleSheet("""
            QPushButton {
                background-color: #1f1a30;
                border: none;
                border-radius: 40px;
            }
            QPushButton:hover {
                background-color: #2c2049;
            }
            QPushButton:pressed {
                background-color: #140f24;
            }
        """)

        self.shadow = QGraphicsDropShadowEffect()
        self.shadow.setBlurRadius(100)
        self.shadow.setXOffset(0)
        self.shadow.setYOffset(0)
        self.shadow.setColor(QColor(0, 0, 0, 220))
        self.setGraphicsEffect(self.shadow)

        self.setAttribute(Qt.WidgetAttribute.WA_Hover, True)

class UpdateButton(QPushButton):
    def __init__(self, text="Update"):
        super().__init__(text)
        self.setCursor(Qt.PointingHandCursor)
        self.setFixedSize(80, 30)
        
        self.setStyleSheet("""
            QPushButton {
                background-color: #2a1e42;
                color: #b388ff;
                border: 1px solid #7c4dff;
                border-radius: 8px;
                padding: 2px 8px;
                font-size: 11px;
                font-weight: bold;
            }
            QPushButton:hover {
                background-color: #3a2a5a;
                border: 1px solid #9c6dff;
            }
            QPushButton:pressed {
                background-color: #1a1230;
            }
        """)
        
        glow = QGraphicsDropShadowEffect()
        glow.setBlurRadius(8)
        glow.setColor(QColor(156, 109, 255, 120))
        glow.setOffset(0, 0)
        self.setGraphicsEffect(glow)

class IconOnlyButtonApp(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("FAIL UP")
        self.setWindowIcon(QIcon(ICON("ai.ico")))
        self.setMinimumSize(800, 600)
        self.resize(800, 600)

        self.retry_count = 0
        self.max_retries = 5
        self.current_video_id = None
        
        self.settings = QSettings("Abhiiishek-rana", "FAIL-UP")
        self.current_llm_type = self.settings.value("llm_type", "local")
        self.current_model = self.settings.value("model", "qwen3:4b")
        self.api_key = self.settings.value("api_key", "")
        
        self.previous_size = QSize(800, 600)
        self.previous_state = Qt.WindowNoState

        self.apply_gradient_background(self.height())

        self.stacked_layout = QStackedLayout()
        self.setLayout(self.stacked_layout)

        self.create_main_view()
        self.create_youtube_view()
        self.create_pdf_views()
        self.create_settings_ui()

        self.youtube_notes_view = None

        self.stacked_layout.setCurrentWidget(self.main_view)
        
        self.current_notification = None
        self.notification_timer = QTimer()
        self.notification_timer.setSingleShot(True)
        self.notification_timer.timeout.connect(self.clear_notification)

    def create_main_view(self):
        self.main_view = QWidget()
        layout = QHBoxLayout(self.main_view)
        layout.setContentsMargins(80, 0, 80, 0)
        layout.setSpacing(100)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self.notes_button = ShadowButton(ICON("note.png"))
        self.notes_button.clicked.connect(self.show_pdf_list_view)
        layout.addWidget(self.notes_button)
        
        button2 = ShadowButton(ICON("yt.svg"))
        button2.clicked.connect(self.show_youtube)
        layout.addWidget(button2)

        self.settings_button = QPushButton(self.main_view)
        self.settings_button.setIcon(QIcon(ICON("settings.png")))
        self.settings_button.setIconSize(QSize(50, 50))
        self.settings_button.setFixedSize(50, 50)
        self.settings_button.setStyleSheet("""
        QPushButton {
            border: none;
            border-radius: 25px;
            background-color: #1f1f1f;
        }
        QPushButton:hover {
            background-color: #333333;
        }
        """)
        self.settings_button.clicked.connect(self.switch_to_settings_view)
        
        self.beta_badge = QLabel("BETA", self.main_view)
        self.beta_badge.setFixedSize(60, 26)
        self.beta_badge.setAlignment(Qt.AlignCenter)
        self.beta_badge.setStyleSheet("""
            QLabel {
                color: #00ff88;
                font-size: 11px;
                font-weight: bold;
                border: 1.5px solid #00ff88;
                border-radius: 6px;
                background-color: rgba(0, 255, 136, 0.08);
                padding: 2px 4px;
            }
        """)
        glow = QGraphicsDropShadowEffect()
        glow.setBlurRadius(25)
        glow.setColor(QColor(0, 255, 136, 180))
        glow.setOffset(0, 0)
        self.beta_badge.setGraphicsEffect(glow)
        self.beta_badge.setToolTip(
            "Thanks for joining the beta!\nThis version is still in development so frequent updates will be coming, keep updating for better experience.\nYour feedback helps us improve the app and shape the final experience. We appreciate your input!"
        )

        self.update_top_right_widgets()
        self.stacked_layout.addWidget(self.main_view)

    def create_pdf_views(self):
        self.pdf_list_view = QWidget()
        list_layout = QVBoxLayout(self.pdf_list_view)
        list_layout.setContentsMargins(20, 20, 20, 20)
        
        title_label = QLabel("Your Notes")
        title_label.setStyleSheet("""
            QLabel {
                color: #b388ff;
                font-size: 24px;
                font-weight: bold;
                margin-bottom: 20px;
            }
        """)
        list_layout.addWidget(title_label)
        
        self.pdf_list = QListWidget()
        self.pdf_list.setStyleSheet("""
            QListWidget {
                background-color: #1a1426;
                color: #e0e0e0;
                border: 1px solid #3a0841;
                border-radius: 5px;
                font-size: 14px;
            }
        """)
        self.pdf_list.setItemDelegate(PDFListDelegate(self))
        self.pdf_list.itemClicked.connect(self.show_pdf_viewer_view)
        
        self.pdf_list.setContextMenuPolicy(Qt.CustomContextMenu)
        self.pdf_list.customContextMenuRequested.connect(self.show_pdf_context_menu)
        
        list_layout.addWidget(self.pdf_list)
        
        back_button = QPushButton("Back to Main")
        back_button.setFixedSize(150, 40)
        back_button.setStyleSheet("""
            QPushButton {
                background-color: #2a1e42;
                color: #b388ff;
                border: none;
                border-radius: 20px;
                padding: 8px 12px;
                font-size: 14px;
                font-weight: bold;
            }
            QPushButton:hover {
                background-color: #3a2a5a;
            }
            QPushButton:pressed {
                background-color: #1a1230;
            }
        """)
        back_button.clicked.connect(self.switch_back_to_main_view)
        list_layout.addWidget(back_button, 0, Qt.AlignLeft)
        
        self.stacked_layout.addWidget(self.pdf_list_view)
        
        self.pdf_viewer_view = QWidget()
        viewer_layout = QVBoxLayout(self.pdf_viewer_view)
        viewer_layout.setContentsMargins(0, 0, 0, 0)
        
        self.pdf_web_view = QWebEngineView()
        settings = self.pdf_web_view.settings()
        settings.setAttribute(QWebEngineSettings.PluginsEnabled, True)
        settings.setAttribute(QWebEngineSettings.PdfViewerEnabled, True)
        
        back_button = QPushButton("← Back to List")
        back_button.setFixedSize(120, 40)
        back_button.setStyleSheet("""
            QPushButton {
                background-color: #2a1e42;
                color: #b388ff;
                border: none;
                border-radius: 20px;
                padding: 8px 12px;
                font-size: 14px;
                font-weight: bold;
            }
            QPushButton:hover {
                background-color: #3a2a5a;
            }
        """)
        back_button.clicked.connect(self.show_pdf_list_view)
        
        viewer_layout.addWidget(back_button)
        viewer_layout.addWidget(self.pdf_web_view)
        
        self.stacked_layout.addWidget(self.pdf_viewer_view)

    def show_pdf_list_view(self):
        self.load_pdf_list()
        self.stacked_layout.setCurrentWidget(self.pdf_list_view)

    def show_pdf_viewer_view(self, item):
        pdf_path = item.data(Qt.UserRole)
        pdf_url = QUrl.fromLocalFile(os.path.abspath(pdf_path))
        self.pdf_web_view.load(pdf_url)
        self.stacked_layout.setCurrentWidget(self.pdf_viewer_view)

    def load_pdf_list(self):
        if hasattr(self, 'web_view'):
            self.web_view.page().runJavaScript("""
            var videos = document.getElementsByTagName('video');
            for (var i = 0; i < videos.length; i++) {
                videos[i].pause();
            }
            var audios = document.getElementsByTagName('audio');
            for (var i = 0; i < audios.length; i++) {
                audios[i].pause();
            }
        """)
        self.pdf_list.clear()
        
        if not os.path.exists("output"):
            os.makedirs("output")
            return
        
        pdf_files = [f for f in os.listdir("output") if f.lower().endswith('.pdf')]
        for pdf_file in sorted(pdf_files, reverse=True):
            item = QListWidgetItem(pdf_file)
            item.setData(Qt.UserRole, os.path.join("output", pdf_file))
            self.pdf_list.addItem(item)

    def show_pdf_context_menu(self, position):
        item = self.pdf_list.itemAt(position)
        if not item:
            return
        
        menu = QMenu()
        
       
        delete_action = QAction("Delete", self)
        delete_action.triggered.connect(lambda: self.delete_pdf(item))
        menu.addAction(delete_action)
        
      
        open_folder_action = QAction("Open Containing Folder", self)
        open_folder_action.triggered.connect(lambda: self.open_containing_folder(item))
        menu.addAction(open_folder_action)
        
        menu.exec_(self.pdf_list.viewport().mapToGlobal(position))


    def delete_pdf(self, item):
        pdf_path = item.data(Qt.UserRole)
        
  
        reply = QMessageBox.question(
            self, 'Delete PDF',
            f"Are you sure you want to delete '{os.path.basename(pdf_path)}'?",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No
        )
        
        if reply == QMessageBox.Yes:
            try:
                os.remove(pdf_path)
                self.pdf_list.takeItem(self.pdf_list.row(item))
                self.show_notification(f"Deleted: {os.path.basename(pdf_path)}")
            except Exception as e:
                self.show_notification(f"Error deleting file: {str(e)}")

    def open_containing_folder(self, item):
        pdf_path = item.data(Qt.UserRole)
        folder_path = os.path.dirname(pdf_path)
        
        try:
            if sys.platform == "win32":
                os.startfile(folder_path)
            elif sys.platform == "darwin":
                subprocess.run(["open", folder_path])
            else:
                subprocess.run(["xdg-open", folder_path])
        except Exception as e:
            self.show_notification(f"Error opening folder: {str(e)}")

    def create_youtube_view(self):
        self.youtube_view = QWidget()
        layout = QVBoxLayout(self.youtube_view)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        
        nav_bar = QWidget()
        nav_bar.setFixedHeight(60)
        nav_bar.setStyleSheet("background-color: rgba(31, 26, 48, 0.9);")
        nav_layout = QHBoxLayout(nav_bar)
        nav_layout.setContentsMargins(15, 0, 15, 0)
        nav_layout.setSpacing(10)
        
        self.youtube_back_button = QPushButton("Back")
        self.youtube_back_button.setFixedSize(100, 40)
        self.youtube_back_button.setStyleSheet("""
            QPushButton {
                background-color: #2a1e42;
                color: #b388ff;
                border: none;
                border-radius: 20px;
                padding: 8px 12px;
                font-size: 14px;
                font-weight: bold;
            }
            QPushButton:hover {
                background-color: #3a2a5a;
            }
            QPushButton:pressed {
                background-color: #1a1230;
            }
        """)
        back_glow = QGraphicsDropShadowEffect()
        back_glow.setBlurRadius(10)
        back_glow.setColor(QColor(156, 109, 255, 150))
        back_glow.setOffset(0, 0)
        self.youtube_back_button.setGraphicsEffect(back_glow)
        self.youtube_back_button.clicked.connect(self.switch_back_to_main_view)
        nav_layout.addWidget(self.youtube_back_button)
        
        self.youtube_home_button = QPushButton("YouTube Home")
        self.youtube_home_button.setFixedSize(150, 40)
        self.youtube_home_button.setStyleSheet("""
            QPushButton {
                background-color: #2a1e42;
                color: #ff6b6b;
                border: none;
                border-radius: 20px;
                padding: 8px 12px;
                font-size: 14px;
                font-weight: bold;
            }
            QPushButton:hover {
                background-color: #3a2a5a;
            }
            QPushButton:pressed {
                background-color: #1a1230;
            }
        """)
        home_glow = QGraphicsDropShadowEffect()
        home_glow.setBlurRadius(10)
        home_glow.setColor(QColor(255, 107, 107, 150))
        home_glow.setOffset(0, 0)
        self.youtube_home_button.setGraphicsEffect(home_glow)
        self.youtube_home_button.clicked.connect(lambda: self.web_view.load(QUrl("https://www.youtube.com")))
        nav_layout.addWidget(self.youtube_home_button)
        
        self.youtube_notes_button = QPushButton("Create Notes")
        self.youtube_notes_button.setFixedSize(150, 40)
        self.youtube_notes_button.setStyleSheet("""
            QPushButton {
                background-color: #2a1e42;
                color: #00ff88;
                border: none;
                border-radius: 20px;
                padding: 8px 12px;
                font-size: 14px;
                font-weight: bold;
            }
            QPushButton:hover {
                background-color: #3a2a5a;
            }
            QPushButton:pressed {
                background-color: #1a1230;
            }
        """)
        notes_glow = QGraphicsDropShadowEffect()
        notes_glow.setBlurRadius(10)
        notes_glow.setColor(QColor(0, 255, 136, 150))
        notes_glow.setOffset(0, 0)
        self.youtube_notes_button.setGraphicsEffect(notes_glow)
        self.youtube_notes_button.clicked.connect(self.create_youtube_notes)
        nav_layout.addWidget(self.youtube_notes_button)
        
        nav_layout.addStretch()
        layout.addWidget(nav_bar)
        
        # Setup persistent YouTube session
        profile_path = os.path.join(BASE_DIR, "yt_profile")
        yt_profile = QWebEngineProfile("YouTubeProfile", self)
        yt_profile.setPersistentCookiesPolicy(QWebEngineProfile.ForcePersistentCookies)
        yt_profile.setCachePath(profile_path)
        yt_profile.setPersistentStoragePath(profile_path)

        yt_profile.settings().setAttribute(QWebEngineSettings.LocalStorageEnabled, True)
        yt_profile.settings().setAttribute(QWebEngineSettings.PluginsEnabled, True)
        yt_profile.settings().setAttribute(QWebEngineSettings.FullScreenSupportEnabled, True)

        self.web_view = QWebEngineView()
        self.web_view.setPage(QWebEnginePage(yt_profile, self.web_view))

        settings = self.web_view.settings()
        settings.setAttribute(QWebEngineSettings.PluginsEnabled, True)
        settings.setAttribute(QWebEngineSettings.FullScreenSupportEnabled, True)
        settings.setAttribute(QWebEngineSettings.JavascriptEnabled, True)
        settings.setAttribute(QWebEngineSettings.PlaybackRequiresUserGesture, False)
        self.web_view.load(QUrl("https://www.youtube.com"))
        self.web_view.loadFinished.connect(self.on_youtube_load_finished)
        layout.addWidget(self.web_view)
        self.stacked_layout.addWidget(self.youtube_view)

    def on_youtube_load_finished(self, success):
        if success:
            self.youtube_notes_button.setEnabled(True)
        else:
            self.youtube_notes_button.setEnabled(False)

    def create_youtube_notes(self):
        if not self.youtube_notes_button.isEnabled():
            self.show_notification("Please wait for YouTube to finish loading")
            return
    
        current_url = self.web_view.url().toString()
    
        try:
            video_id = None
            if "youtube.com/watch" in current_url:
                if "v=" in current_url:
                    video_id = current_url.split("v=")[1].split("&")[0]
                elif "youtu.be/" in current_url:
                    video_id = current_url.split("youtu.be/")[1].split("?")[0]
        
            if not video_id:
                self.show_notification("Please open a YouTube video first")
                return
        
            self.show_notification("Fetching transcript...")
            self.youtube_notes_button.setEnabled(False)
        
            self.retry_count = 0
            self.max_retries = 10
            self.current_video_id = video_id
            self.fetch_transcript_with_retry()
        
        except Exception as e:
            self.youtube_notes_button.setEnabled(True)
            self.show_notification(f"Error: {str(e)}")

    def fetch_transcript_with_retry(self):
        try:
            self.worker_thread = QThread()
            self.worker = TranscriptWorker(self.current_video_id)
            self.worker.moveToThread(self.worker_thread)
            self.worker.finished.connect(self.on_transcript_finished)
            self.worker.error.connect(self.handle_transcript_error)
            self.worker_thread.started.connect(self.worker.fetch_transcript)
            self.worker_thread.finished.connect(self.worker_thread.deleteLater)
            self.worker_thread.start()
        
        except Exception as e:
            self.handle_transcript_error(e)

    def on_transcript_finished(self, filename):
        self.retry_count = 0
        self.worker_thread.quit()
        self.worker_thread.wait()
        self.youtube_notes_button.setEnabled(True)
    
    
        self.youtube_notes_view = YouTubeNotesView(
            self.web_view, 
            filename,
            llm_type=self.current_llm_type,
            model=self.current_model,
            api_key=self.api_key if self.current_llm_type == "openrouter" else None
        )
        self.youtube_notes_view.parent_window = self
        
       
        if self.stacked_layout.indexOf(self.youtube_notes_view) == -1:
            self.stacked_layout.addWidget(self.youtube_notes_view)
            
        self.stacked_layout.setCurrentWidget(self.youtube_notes_view)
        self.show_notification("Transcript loaded. Generating notes...")


    def handle_transcript_error(self, error_msg):
        self.youtube_notes_button.setEnabled(True)
        if "No transcript found" in str(error_msg):
            msg = "No transcript available for this video"
        elif "Video unavailable" in str(error_msg):
            msg = "Video unavailable or private"
        elif "disabled" in str(error_msg).lower():
            msg = "Transcripts are disabled for this video"
        else:
            msg = f"Error: {str(error_msg)}"
    
        self.show_notification(msg)

    def create_settings_ui(self):
        self.settings_container = QWidget()
        layout = QVBoxLayout(self.settings_container)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        top_bar = QWidget()
        top_bar.setFixedHeight(60)
        top_bar.setStyleSheet("background: transparent;")
        top_bar_layout = QHBoxLayout(top_bar)
        top_bar_layout.setContentsMargins(10, 10, 10, 10)
        top_bar_layout.setSpacing(0)
        top_bar_layout.addStretch()

        self.settings_back_button = QPushButton("← Back to Main")
        self.settings_back_button.setFixedSize(150, 40)
        self.settings_back_button.setStyleSheet("""
        QPushButton {
            background-color: #2a1e42;
            color: #b388ff;
            border: 2px solid #7c4dff;
            border-radius: 20px;
            padding: 8px 15px;
            font-size: 14px;
            font-weight: bold;
        }
        QPushButton:hover {
            background-color: #3a2a5a;
            border: 2px solid #9c6dff;
        }
        QPushButton:pressed {
            background-color: #1a1230;
        }
        """)
        settings_back_glow = QGraphicsDropShadowEffect()
        settings_back_glow.setBlurRadius(15)
        settings_back_glow.setColor(QColor(156, 109, 255, 150))
        settings_back_glow.setOffset(0, 0)
        self.settings_back_button.setGraphicsEffect(settings_back_glow)
        self.settings_back_button.clicked.connect(self.switch_back_to_main_view)
        top_bar_layout.addWidget(self.settings_back_button)
        layout.addWidget(top_bar)

        scroll_area = QScrollArea()
        scroll_area.setWidgetResizable(True)
        scroll_area.setStyleSheet("border: none; background: transparent;")
        
        content_widget = QWidget()
        content_layout = QVBoxLayout(content_widget)
        content_layout.setContentsMargins(20, 10, 20, 20)
        content_layout.setSpacing(15)
        
        llm_group = QGroupBox("LLM Configuration")
        llm_group.setStyleSheet("""
            QGroupBox {
                border: 1px solid #7c4dff;
                border-radius: 8px;
                margin-top: 10px;
                padding-top: 15px;
                color: #b388ff;
                font-weight: bold;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                left: 10px;
                padding: 0 5px;
            }
        """)
        
        llm_layout = QVBoxLayout()
        llm_layout.setSpacing(15)
        
        self.llm_type_group = QButtonGroup()
        self.local_llm_radio = QRadioButton("Local LLM (Ollama)")
        self.openrouter_radio = QRadioButton("OpenRouter")
        self.llm_type_group.addButton(self.local_llm_radio, 0)
        self.llm_type_group.addButton(self.openrouter_radio, 1)
        
        if self.current_llm_type == "local":
            self.local_llm_radio.setChecked(True)
        else:
            self.openrouter_radio.setChecked(True)
        
        self.local_llm_container = QWidget()
        local_llm_layout = QVBoxLayout(self.local_llm_container)
        
        self.model_label = QLabel("Select Model:")
        self.model_label.setStyleSheet("color: #b388ff;")
        self.model_dropdown = QComboBox()
        self.model_dropdown.setStyleSheet("""
            QComboBox {
                background-color: #2a1e42;
                color: #e0e0e0;
                border: 1px solid #7c4dff;
                border-radius: 4px;
                padding: 5px;
            }
            QComboBox::drop-down {
                border: none;
            }
        """)
        
        self.populate_ollama_models()
        
        index = self.model_dropdown.findText(self.current_model)
        if index >= 0:
            self.model_dropdown.setCurrentIndex(index)
        
        local_llm_layout.addWidget(self.model_label)
        local_llm_layout.addWidget(self.model_dropdown)
        local_llm_layout.addStretch()
        
        self.openrouter_container = QWidget()
        openrouter_layout = QVBoxLayout(self.openrouter_container)
        
        self.api_key_label = QLabel("OpenRouter API Key:")
        self.api_key_label.setStyleSheet("color: #b388ff;")
        self.api_key_input = QLineEdit()
        self.api_key_input.setPlaceholderText("Enter your OpenRouter API key")
        self.api_key_input.setEchoMode(QLineEdit.Password)
        self.api_key_input.setText(self.api_key)
        self.api_key_input.setStyleSheet("""
            QLineEdit {
                background-color: #2a1e42;
                color: #e0e0e0;
                border: 1px solid #7c4dff;
                border-radius: 4px;
                padding: 5px;
            }
        """)
        
        self.openrouter_model_label = QLabel("OpenRouter Model:")
        self.openrouter_model_label.setStyleSheet("color: #b388ff;")
        self.openrouter_model_dropdown = QComboBox()
        self.openrouter_model_dropdown.addItems([
            "qwen/qwen3-8b:free",
            "qwen/qwen3-4b:free",
        ])
        self.openrouter_model_dropdown.setStyleSheet("""
            QComboBox {
                background-color: #2a1e42;
                color: #e0e0e0;
                border: 1px solid #7c4dff;
                border-radius: 4px;
                padding: 5px;
            }
            QComboBox::drop-down {
                border: none;
            }
        """)
        
        if self.current_llm_type == "openrouter":
            index = self.openrouter_model_dropdown.findText(self.current_model)
            if index >= 0:
                self.openrouter_model_dropdown.setCurrentIndex(index)
        
        openrouter_layout.addWidget(self.api_key_label)
        openrouter_layout.addWidget(self.api_key_input)
        openrouter_layout.addWidget(self.openrouter_model_label)
        openrouter_layout.addWidget(self.openrouter_model_dropdown)
        openrouter_layout.addStretch()
        
        self.local_llm_container.setVisible(self.current_llm_type == "local")
        self.openrouter_container.setVisible(self.current_llm_type == "openrouter")
        
        self.local_llm_radio.toggled.connect(lambda checked: (
            self.local_llm_container.setVisible(checked),
            self.openrouter_container.setVisible(not checked)
        ))
        self.openrouter_radio.toggled.connect(lambda checked: (
            self.openrouter_container.setVisible(checked),
            self.local_llm_container.setVisible(not checked)
        ))
        
        refresh_button = QPushButton("Refresh Models")
        refresh_button.setFixedHeight(30)
        refresh_button.setStyleSheet("""
            QPushButton {
                background-color: #2a1e42;
                color: #b388ff;
                border: 1px solid #7c4dff;
                border-radius: 4px;
                font-size: 12px;
            }
            QPushButton:hover {
                background-color: #3a2a5a;
            }
        """)
        refresh_button.clicked.connect(self.populate_ollama_models)
        
        save_button = QPushButton("Save Settings")
        save_button.setFixedHeight(40)
        save_button.setStyleSheet("""
            QPushButton {
                background-color: #7c4dff;
                color: white;
                border: none;
                border-radius: 8px;
                font-weight: bold;
                font-size: 14px;
            }
            QPushButton:hover {
                background-color: #9c6dff;
            }
        """)
        save_button.clicked.connect(self.save_settings)
        
        llm_layout.addWidget(self.local_llm_radio)
        llm_layout.addWidget(self.local_llm_container)
        llm_layout.addWidget(self.openrouter_radio)
        llm_layout.addWidget(self.openrouter_container)
        llm_layout.addWidget(refresh_button)
        llm_layout.addWidget(save_button)
        llm_group.setLayout(llm_layout)
        
        content_layout.addWidget(llm_group)
        content_layout.addStretch()
        
        scroll_area.setWidget(content_widget)
        layout.addWidget(scroll_area)
        self.stacked_layout.addWidget(self.settings_container)

    def populate_ollama_models(self):
        self.model_dropdown.clear()
        try:
            result = subprocess.run(['ollama', 'list'], capture_output=True, text=True)
            if result.returncode == 0:
                lines = result.stdout.split('\n')
                if len(lines) > 1:
                    for line in lines[1:]:
                        if line.strip():
                            model_name = line.split()[0]
                            self.model_dropdown.addItem(model_name)
                else:
                    self.model_dropdown.addItem("No models found")
            else:
                self.model_dropdown.addItem("Error fetching models")
                self.show_notification("Failed to fetch Ollama models")
        except FileNotFoundError:
            self.model_dropdown.addItem("Ollama not installed")
            self.show_notification("Ollama is not installed or not in PATH")

    def save_settings(self):
        if self.local_llm_radio.isChecked():
            self.current_llm_type = "local"
            self.current_model = self.model_dropdown.currentText()
            self.api_key = ""
        else:
            self.current_llm_type = "openrouter"
            self.current_model = self.openrouter_model_dropdown.currentText()
            self.api_key = self.api_key_input.text()
            if not self.api_key:
                self.show_notification("Please enter your OpenRouter API key")
                return
        
        self.settings.setValue("llm_type", self.current_llm_type)
        self.settings.setValue("model", self.current_model)
        self.settings.setValue("api_key", self.api_key)
        
        self.show_notification(f"Settings saved. Using {self.current_llm_type} model: {self.current_model}")

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.update_top_right_widgets()
        self.apply_gradient_background(self.height())
        
        if not self.isMaximized() and not self.isFullScreen():
            self.previous_size = event.size()

    def update_top_right_widgets(self):
        if hasattr(self, 'settings_button') and hasattr(self, 'beta_badge'):
            self.settings_button.move(self.width() - 60, 10)
            badge_x = self.settings_button.x() - self.beta_badge.width() - 10
            badge_y = self.settings_button.y() + (self.settings_button.height() - self.beta_badge.height()) // 2
            self.beta_badge.move(badge_x, badge_y)

    def apply_gradient_background(self, height):
        gradient = QLinearGradient(0, 0, 0, height)
        gradient.setColorAt(0.0, QColor("#0e0d2b"))
        gradient.setColorAt(1.0, QColor("#3a0841"))
        palette = QPalette()
        palette.setBrush(QPalette.Window, QBrush(gradient))
        self.setAutoFillBackground(True)
        self.setPalette(palette)

    def show_youtube(self):
        self.clear_notification()
        self.previous_state = self.windowState()
        self.previous_size = self.size()

        self.setMinimumSize(1000, 700)

   
        if self.youtube_notes_view:
            self.stacked_layout.removeWidget(self.youtube_notes_view)
            self.youtube_notes_view.deleteLater()
            self.youtube_notes_view = None

  
        self.stacked_layout.setCurrentWidget(self.youtube_view)

 
        self.web_view.show()
        self.web_view.load(QUrl("https://www.youtube.com"))

    
        if self.previous_state & Qt.WindowFullScreen:
            self.showFullScreen()
        elif self.previous_state & Qt.WindowMaximized:
            self.showMaximized()
        else:
            self.resize(1000, 700)


    def switch_to_settings_view(self):
        self.clear_notification()
        self.previous_state = self.windowState()
        self.previous_size = self.size()
        self.stacked_layout.setCurrentWidget(self.settings_container)

    def switch_back_to_main_view(self):
        if hasattr(self, 'web_view'):
            try:
                self.web_view.page().runJavaScript("""
                var videos = document.getElementsByTagName('video');
                for (var i = 0; i < videos.length; i++) {
                    videos[i].pause();
                }
                var audios = document.getElementsByTagName('audio');
                for (var i = 0; i < audios.length; i++) {
                    audios[i].pause();
                }
            """)
            except RuntimeError:
                pass 

   
        if self.youtube_notes_view:
            self.youtube_view.layout().addWidget(self.web_view) 
            self.stacked_layout.removeWidget(self.youtube_notes_view)
            self.youtube_notes_view.deleteLater()
            self.youtube_notes_view = None

        self.clear_notification()

        if self.previous_state & Qt.WindowFullScreen:
            self.showFullScreen()
        elif self.previous_state & Qt.WindowMaximized:
            self.showMaximized()
        else:
            self.showNormal()
            self.resize(self.previous_size)

        self.setMinimumSize(800, 600)
        self.stacked_layout.setCurrentWidget(self.main_view)

    def show_notification(self, message):
        self.clear_notification()
        
        current_widget = self.stacked_layout.currentWidget()
        self.current_notification = QLabel(message, current_widget)
        self.current_notification.setStyleSheet("""
            QLabel {
                background-color: rgba(156, 109, 255, 0.2);
                color: #b388ff;
                border: 1px solid #7c4dff;
                border-radius: 10px;
                padding: 10px 20px;
                font-size: 14px;
            }
        """)
        self.current_notification.adjustSize()
        self.current_notification.move((current_widget.width() - self.current_notification.width()) // 2, 20)
        self.current_notification.show()
        
        self.notification_timer.start(3000)
        
    def clear_notification(self):
        if self.current_notification:
            try:
                self.current_notification.hide()
                self.current_notification.deleteLater()
            except:
                pass
            finally:
                self.current_notification = None

if __name__ == "__main__":
    app = QApplication(sys.argv)
    app.setStyleSheet("""
        QToolTip {
            background-color: #1f1a30;
            color: #00ff88;
            border: 1px solid #00ff88;
            padding: 5px;
            font-size: 11px;
            border-radius: 4px;
        }
    """)

    splash = SplashScreen()
    splash.show()

    def launch_main_app():
        global main_window
        main_window = IconOnlyButtonApp()
        main_window.show()

    splash.finished.connect(launch_main_app)

    sys.exit(app.exec())

