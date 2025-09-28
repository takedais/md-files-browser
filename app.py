#!/usr/bin/env python3
"""
Markdown Document Browser
プロジェクト内のMDファイルを一覧表示・閲覧するWebアプリケーション
"""

from flask import Flask, render_template, jsonify, send_from_directory, request, Response, send_file
from flask_cors import CORS
import os
import json
from pathlib import Path
from datetime import datetime
import markdown2
import chardet
import re
from typing import List, Dict, Optional
import weasyprint
import tempfile
from io import BytesIO

app = Flask(__name__)
CORS(app)
app.config['JSON_AS_ASCII'] = False  # 日本語を正しくJSONで返す

# 設定ファイルのパス
CONFIG_FILE = "md_browser_config.json"

class MDFileBrowser:
    """MDファイルブラウザークラス"""

    def __init__(self):
        self.config = self.load_config()
        self.markdown_renderer = markdown2.Markdown(
            extras=[
                "fenced-code-blocks",
                "tables",
                "strike",
                "task-list",
                "code-friendly",
                "header-ids",
                "toc"
            ]
        )

    def load_config(self) -> dict:
        """設定ファイルを読み込む"""
        if os.path.exists(CONFIG_FILE):
            with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        else:
            # デフォルト設定
            default_config = {
                "projects": [
                    {
                        "name": "Voice Memo v2",
                        "path": "/home/takedais/voicememo-v2",
                        "description": "医療音声文字起こしシステム",
                        "color": "#2196f3"
                    },
                    {
                        "name": "Medical Text Rules",
                        "path": "/home/takedais/medical-text-rules",
                        "description": "医療テキスト処理",
                        "color": "#4caf50"
                    },
                    {
                        "name": "Voice-to-Text Dictionary",
                        "path": "/home/takedais/voice-to-text-making-dic",
                        "description": "音声認識辞書構築",
                        "color": "#ff9800"
                    }
                ],
                "file_patterns": {
                    "important": ["README.md", "CLAUDE.md", "*_REPORT.md", "*_PLAN.md"],
                    "documentation": ["*_SUMMARY.md", "*.md"],
                },
                "excluded_dirs": ["node_modules", "venv", ".git", "__pycache__"],
                "max_file_size_kb": 5000,
                "recent_files_count": 10
            }
            self.save_config(default_config)
            return default_config

    def save_config(self, config: dict):
        """設定を保存"""
        with open(CONFIG_FILE, 'w', encoding='utf-8') as f:
            json.dump(config, f, ensure_ascii=False, indent=2)

    def get_project_md_files(self, project_path: str, recursive: bool = True) -> List[Dict]:
        """プロジェクト内のMDファイルを取得"""
        md_files = []
        path = Path(project_path)

        if not path.exists():
            return []

        # 除外ディレクトリのセット
        excluded = set(self.config.get("excluded_dirs", []))

        if recursive:
            # 再帰的に検索
            for md_file in path.rglob("*.md"):
                # 除外ディレクトリをチェック
                if any(excluded_dir in md_file.parts for excluded_dir in excluded):
                    continue

                md_files.append(self.get_file_info(md_file))
        else:
            # 直下のみ
            for md_file in path.glob("*.md"):
                md_files.append(self.get_file_info(md_file))

        # 重要度でソート
        md_files.sort(key=lambda x: (x['priority'], x['modified']), reverse=True)

        return md_files

    def get_file_info(self, file_path: Path) -> Dict:
        """ファイル情報を取得"""
        stat = file_path.stat()

        # ファイルの重要度を判定
        priority = self.get_file_priority(file_path.name)

        # ファイルの最初の数行を取得（プレビュー用）
        preview = self.get_file_preview(file_path)

        return {
            "name": file_path.name,
            "path": str(file_path),
            "relative_path": str(file_path.relative_to(file_path.parent.parent)
                                 if file_path.parent.parent.exists() else file_path.name),
            "size": stat.st_size,
            "size_kb": round(stat.st_size / 1024, 2),
            "modified": datetime.fromtimestamp(stat.st_mtime).isoformat(),
            "modified_human": datetime.fromtimestamp(stat.st_mtime).strftime("%Y/%m/%d %H:%M"),
            "priority": priority,
            "preview": preview
        }

    def get_file_priority(self, filename: str) -> int:
        """ファイルの重要度を判定"""
        important_patterns = self.config.get("file_patterns", {}).get("important", [])

        for pattern in important_patterns:
            if pattern.replace("*", "") in filename.upper():
                return 1  # 最高優先度

        if filename.upper() == "README.MD":
            return 2
        elif "REPORT" in filename.upper():
            return 3
        elif "PLAN" in filename.upper():
            return 4
        elif "SUMMARY" in filename.upper():
            return 5

        return 10  # 通常優先度

    def get_file_preview(self, file_path: Path, lines: int = 3) -> str:
        """ファイルのプレビューを取得"""
        try:
            # まずUTF-8で試す
            try:
                with open(file_path, 'r', encoding='utf-8') as f:
                    content_lines = f.readlines()[:lines + 5]  # ヘッダーをスキップするため多めに取得
            except UnicodeDecodeError:
                # UTF-8で失敗したら強制的に読み込み
                with open(file_path, 'r', encoding='utf-8', errors='replace') as f:
                    content_lines = f.readlines()[:lines + 5]

            # マークダウンのヘッダーをスキップ
            preview_lines = []
            for line in content_lines:
                if not line.startswith('#') and line.strip():
                    preview_lines.append(line.strip())
                    if len(preview_lines) >= lines:
                        break

            preview = ' '.join(preview_lines)
            # 長すぎる場合は切り詰め
            if len(preview) > 200:
                preview = preview[:200] + "..."

            return preview
        except Exception as e:
            return f"プレビュー取得エラー: {str(e)}"

    def read_md_file(self, file_path: str) -> Dict:
        """MDファイルを読み込んでHTMLに変換"""
        try:
            path = Path(file_path)

            if not path.exists():
                return {"error": "ファイルが見つかりません"}

            # まずUTF-8で読み込みを試みる
            content = None
            encoding_used = 'utf-8'

            try:
                with open(path, 'r', encoding='utf-8') as f:
                    content = f.read()
                    encoding_used = 'utf-8'
            except UnicodeDecodeError:
                # UTF-8で失敗したらエンコーディングを自動検出
                with open(path, 'rb') as f:
                    raw_data = f.read()
                    result = chardet.detect(raw_data)
                    detected_encoding = result['encoding']

                    # 検出されたエンコーディングで読み込み
                    if detected_encoding:
                        try:
                            content = raw_data.decode(detected_encoding)
                            encoding_used = detected_encoding
                        except:
                            # それでも失敗したらUTF-8で強制的に読み込み（エラー文字は置換）
                            content = raw_data.decode('utf-8', errors='replace')
                            encoding_used = 'utf-8 (forced)'
                    else:
                        # エンコーディング検出失敗時はUTF-8で強制読み込み
                        content = raw_data.decode('utf-8', errors='replace')
                        encoding_used = 'utf-8 (forced)'

            # 目次を生成（先に生成してIDを取得）
            toc = self.generate_toc(content)

            # マークダウンをHTMLに変換
            html_content = self.markdown_renderer.convert(content)

            # 見出しにIDを追加（日本語対応）
            for item in toc:
                # 見出しタグを検索して、IDを追加
                pattern = f"<h{item['level']}>({re.escape(item['title'])})</h{item['level']}>"
                replacement = f"<h{item['level']} id=\"{item['anchor']}\">{item['title']}</h{item['level']}>"
                html_content = re.sub(pattern, replacement, html_content)

            # ファイル情報
            file_info = self.get_file_info(path)

            return {
                "content": content,
                "html": html_content,
                "toc": toc,
                "info": file_info,
                "encoding": encoding_used
            }

        except Exception as e:
            return {"error": f"ファイル読み込みエラー: {str(e)}"}

    def generate_toc(self, content: str) -> List[Dict]:
        """目次を生成"""
        toc = []
        lines = content.split('\n')

        for line in lines:
            # マークダウンの見出しを検出
            match = re.match(r'^(#{1,6})\s+(.+)$', line)
            if match:
                level = len(match.group(1))
                title = match.group(2)
                # markdown2のheader-idsと同じ方法でIDを生成
                # 日本語を含む場合も考慮
                import unicodedata
                # NFKDで分解して、ASCIIに変換可能な文字のみを残す
                anchor = unicodedata.normalize('NFKD', title.lower())
                anchor = re.sub(r'[^\w\s-]', '', anchor)
                anchor = re.sub(r'[-\s]+', '-', anchor).strip('-')
                # 空になった場合は、簡単なハッシュを使用
                if not anchor:
                    import hashlib
                    anchor = 'heading-' + hashlib.md5(title.encode()).hexdigest()[:8]

                toc.append({
                    "level": level,
                    "title": title,
                    "anchor": anchor
                })

        return toc

    def search_in_files(self, query: str, project_path: Optional[str] = None) -> List[Dict]:
        """ファイル内を検索"""
        results = []
        query_lower = query.lower()

        if project_path:
            projects = [{"path": project_path}]
        else:
            projects = self.config.get("projects", [])

        for project in projects:
            path = Path(project["path"])
            if not path.exists():
                continue

            for md_file in path.rglob("*.md"):
                # 除外ディレクトリをチェック
                excluded = set(self.config.get("excluded_dirs", []))
                if any(excluded_dir in md_file.parts for excluded_dir in excluded):
                    continue

                try:
                    with open(md_file, 'r', encoding='utf-8', errors='ignore') as f:
                        content = f.read()

                    if query_lower in content.lower():
                        # マッチした行を取得
                        lines = content.split('\n')
                        matches = []

                        for i, line in enumerate(lines):
                            if query_lower in line.lower():
                                matches.append({
                                    "line_number": i + 1,
                                    "line": line.strip()[:100],  # 最初の100文字
                                })

                        if matches:
                            file_info = self.get_file_info(md_file)
                            file_info["matches"] = matches[:5]  # 最初の5つのマッチ
                            file_info["total_matches"] = len(matches)
                            results.append(file_info)

                except Exception:
                    continue

        return results

    def get_recent_files(self) -> List[Dict]:
        """最近更新されたファイルを取得"""
        all_files = []

        for project in self.config.get("projects", []):
            files = self.get_project_md_files(project["path"], recursive=True)
            for file in files:
                file["project"] = project["name"]
                all_files.append(file)

        # 更新日時でソート
        all_files.sort(key=lambda x: x['modified'], reverse=True)

        # 指定数だけ返す
        count = self.config.get("recent_files_count", 10)
        return all_files[:count]

    def generate_pdf(self, file_path: str) -> BytesIO:
        """MDファイルからPDFを生成"""
        # ファイルを読み込む
        file_data = self.read_md_file(file_path)

        if file_data.get("error"):
            return None

        # HTML生成
        html_template = """
        <!DOCTYPE html>
        <html lang="ja">
        <head>
            <meta charset="UTF-8">
            <style>
                @page {
                    size: A4;
                    margin: 20mm;
                    @bottom-center {
                        content: counter(page) " / " counter(pages);
                        font-size: 10pt;
                        color: #666;
                    }
                }

                body {
                    font-family: 'Noto Sans CJK JP', 'Hiragino Sans', 'Yu Gothic', sans-serif;
                    line-height: 1.8;
                    color: #333;
                }

                h1 {
                    color: #2c3e50;
                    border-bottom: 2px solid #3498db;
                    padding-bottom: 10px;
                    margin: 30px 0 20px;
                    page-break-after: avoid;
                }

                h2 {
                    color: #34495e;
                    border-bottom: 1px solid #bdc3c7;
                    padding-bottom: 8px;
                    margin: 25px 0 15px;
                    page-break-after: avoid;
                }

                h3 {
                    color: #34495e;
                    margin: 20px 0 10px;
                    page-break-after: avoid;
                }

                p {
                    margin: 12px 0;
                    text-align: justify;
                }

                code {
                    background: #f5f5f5;
                    padding: 2px 6px;
                    border-radius: 3px;
                    font-family: 'Consolas', 'Monaco', monospace;
                    font-size: 0.9em;
                }

                pre {
                    background: #f8f8f8;
                    border: 1px solid #ddd;
                    padding: 15px;
                    border-radius: 5px;
                    overflow-x: auto;
                    page-break-inside: avoid;
                    margin: 15px 0;
                }

                pre code {
                    background: none;
                    padding: 0;
                }

                table {
                    border-collapse: collapse;
                    width: 100%;
                    margin: 20px 0;
                    page-break-inside: avoid;
                }

                th, td {
                    border: 1px solid #ddd;
                    padding: 10px 15px;
                    text-align: left;
                }

                th {
                    background: #f8f9fa;
                    font-weight: bold;
                }

                tr:nth-child(even) {
                    background: #f9f9f9;
                }

                ul, ol {
                    margin: 15px 0;
                    padding-left: 30px;
                }

                li {
                    margin: 5px 0;
                }

                blockquote {
                    border-left: 4px solid #3498db;
                    padding-left: 20px;
                    margin: 20px 0;
                    color: #666;
                    font-style: italic;
                }

                a {
                    color: #3498db;
                    text-decoration: none;
                }

                .header {
                    text-align: center;
                    margin-bottom: 40px;
                    padding-bottom: 20px;
                    border-bottom: 2px solid #3498db;
                }

                .header h1 {
                    border: none;
                    margin: 0;
                    padding: 0;
                    font-size: 2em;
                }

                .metadata {
                    margin-top: 10px;
                    font-size: 0.9em;
                    color: #666;
                }

                .footer {
                    margin-top: 50px;
                    padding-top: 20px;
                    border-top: 1px solid #ddd;
                    text-align: center;
                    font-size: 0.85em;
                    color: #666;
                }
            </style>
        </head>
        <body>
            <div class="header">
                <h1>{title}</h1>
                <div class="metadata">
                    生成日: {date}<br>
                    エンコーディング: {encoding}
                </div>
            </div>

            {content}

            <div class="footer">
                MD Document Browser でエクスポート
            </div>
        </body>
        </html>
        """

        # HTMLを生成
        html_content = html_template.format(
            title=file_data['info']['name'],
            date=datetime.now().strftime('%Y年%m月%d日 %H:%M'),
            encoding=file_data.get('encoding', 'UTF-8'),
            content=file_data['html']
        )

        # PDFを生成
        pdf_buffer = BytesIO()

        # WeasyprintでPDF生成
        doc = weasyprint.HTML(string=html_content)
        doc.write_pdf(pdf_buffer)

        pdf_buffer.seek(0)
        return pdf_buffer


# グローバルインスタンス
browser = MDFileBrowser()


@app.route('/')
def index():
    """メインページ"""
    response = Response(render_template('index.html'))
    response.headers['Content-Type'] = 'text/html; charset=utf-8'
    return response


@app.route('/api/projects')
def get_projects():
    """プロジェクト一覧を取得"""
    return jsonify(browser.config.get("projects", []))


@app.route('/api/files/<path:project_path>')
def get_files(project_path):
    """指定プロジェクトのMDファイル一覧を取得"""
    recursive = request.args.get('recursive', 'false').lower() == 'true'
    files = browser.get_project_md_files(f"/{project_path}", recursive)
    return jsonify(files)


@app.route('/api/file')
def read_file():
    """MDファイルを読み込む"""
    file_path = request.args.get('path')
    if not file_path:
        return jsonify({"error": "ファイルパスが指定されていません"}), 400

    result = browser.read_md_file(file_path)
    return jsonify(result)


@app.route('/api/search')
def search():
    """ファイル内検索"""
    query = request.args.get('q', '')
    project_path = request.args.get('project', None)

    if not query:
        return jsonify([])

    results = browser.search_in_files(query, project_path)
    return jsonify(results)


@app.route('/api/recent')
def get_recent():
    """最近更新されたファイルを取得"""
    files = browser.get_recent_files()
    return jsonify(files)


@app.route('/api/config', methods=['GET', 'POST'])
def config():
    """設定の取得・更新"""
    if request.method == 'GET':
        return jsonify(browser.config)
    else:
        new_config = request.json
        browser.save_config(new_config)
        browser.config = new_config
        return jsonify({"status": "success"})


@app.route('/api/pdf')
def generate_pdf():
    """MDファイルをPDFに変換してダウンロード"""
    file_path = request.args.get('path')

    if not file_path:
        return jsonify({"error": "ファイルパスが指定されていません"}), 400

    try:
        pdf_buffer = browser.generate_pdf(file_path)

        if not pdf_buffer:
            return jsonify({"error": "PDF生成に失敗しました"}), 500

        # ファイル名を生成
        filename = Path(file_path).stem + '.pdf'

        return send_file(
            pdf_buffer,
            mimetype='application/pdf',
            as_attachment=True,
            download_name=filename
        )
    except Exception as e:
        return jsonify({"error": f"PDF生成エラー: {str(e)}"}), 500


if __name__ == '__main__':
    # テンプレートディレクトリを作成
    os.makedirs('templates', exist_ok=True)
    os.makedirs('static', exist_ok=True)

    print("="*60)
    print("📚 Markdown Document Browser")
    print("="*60)
    print("起動中... http://localhost:5555")
    print("Ctrl+C で終了")
    print("="*60)

    app.run(host='0.0.0.0', port=5555, debug=True)