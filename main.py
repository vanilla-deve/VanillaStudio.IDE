import os
import sys
import subprocess
import tempfile
import threading
import webbrowser
from pathlib import Path
import tkinter as tk
from tkinter import ttk, filedialog, messagebox, simpledialog
from tkinter.scrolledtext import ScrolledText
import re
import time

# Try to import Pygments for improved highlighting
try:
    from pygments import lex
    from pygments.lexers import (
        PythonLexer, CLexer, CppLexer, HtmlLexer, CssLexer, JavascriptLexer,
        RustLexer, JavaLexer, LuaLexer, TypeScriptLexer, GoLexer, 
        CSharpLexer, RubyLexer, KotlinLexer, NixLexer
    )
    from pygments.token import Token
    USE_PYGMENTS = True
except Exception:
    USE_PYGMENTS = False

APP_TITLE = "Vanilla Studio IDE"

# default extension mapping by language 
LANG_EXT = {
    "python": ".py",
    "c": ".c",
    "cpp": ".cpp",
    "html": ".html",
    "css": ".css",
    "javascript": ".js",
    "js": ".js",
    "typescript": ".ts", 
    "ts": ".ts",
    "rust": ".rs",
    "java": ".java",
    "lua": ".lua",
    "go": ".go",
    "csharp": ".cs",
    "cs": ".cs",
    "ruby": ".rb",
    "rb": ".rb",
    "kotlin": ".kt",
    "kt": ".kt",
    "nix": ".nix",
}

# ---------------------------
# Helpers
# ---------------------------
def safe_run_subprocess(cmd, cwd=None, timeout=60):
    """Run subprocess and return (returncode, stdout, stderr)"""
    try:
        proc = subprocess.run(cmd, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, shell=False, timeout=timeout, text=True)
        return proc.returncode, proc.stdout, proc.stderr
    except Exception as e:
        return -1, "", str(e)

def which(executable):
    """Check if executable is in PATH"""
    from shutil import which as _which
    return _which(executable) is not None

# ---------------------------
# Editor Tab class
# ---------------------------
class EditorTab:
    def __init__(self, master_notebook, app, title="untitled", filepath=None, language="python"):
        self.app = app
        self.notebook = master_notebook
        self.frame = ttk.Frame(self.notebook)
        self.filepath = Path(filepath) if filepath else None
        self.language = language.lower()
        self.title = title

        # Left: linenumbers, Center: text
        self.linenumbers = tk.Text(self.frame, width=4, padx=4, takefocus=0, border=0,
                                   background="#333333", foreground="#bbb", state="disabled", wrap="none")
        self.linenumbers.pack(side="left", fill="y")

        self.text = tk.Text(self.frame, wrap="none", undo=True, autoseparators=True, maxundo=-1)
        self.text.pack(side="left", fill="both", expand=True)

        # Scrollbars
        self.vbar = ttk.Scrollbar(self.frame, orient="vertical", command=self._on_vscroll)
        self.vbar.pack(side="right", fill="y")
        self.text.config(yscrollcommand=self._on_textscroll)
        self.linenumbers.config(yscrollcommand=self._on_textscroll_ln)

        self.hbar = ttk.Scrollbar(self.frame, orient="horizontal", command=self.text.xview)
        self.hbar.pack(side="bottom", fill="x")
        self.text.config(xscrollcommand=self.hbar.set)

        # Tag styles
        self._setup_tags()

        # Keybindings: editing helpers
        self.text.bind("<KeyRelease>", self.on_key_release)
        self.text.bind("<Return>", self.on_return_key)
        self.text.bind("<Tab>", self.on_tab_key)
        self.text.bind("<BackSpace>", self.on_backspace)
        self.text.bind("<Control-slash>", self.toggle_comment)
        self.text.bind("<Configure>", lambda e: self.update_linenumbers())
        self.text.bind("<Button-1>", lambda e: self.schedule_update())
        # Mouse wheels
        self.text.bind("<MouseWheel>", lambda e: self._on_mousewheel(e))
        self.text.bind("<Button-4>", lambda e: self._on_mousewheel(e))
        self.text.bind("<Button-5>", lambda e: self._on_mousewheel(e))

        # Autopairs: intercept keypress (before default insertion)
        self.text.bind("<KeyPress>", self._on_keypress, add=True)

        # Trackers for delayed updates
        self._highlight_after_id = None
        self._update_ln_after_id = None

        # Insert sample text for new file
        if not self.filepath:
            sample = self._sample_for_language()
            if sample:
                self.text.insert("1.0", sample)

        # initial update
        self.update_linenumbers()
        self.highlight_syntax()

    def _on_mousewheel(self, event):
        # keep line numbers scrolling in sync
        if hasattr(event, "delta"):
            if event.delta < 0:
                self.text.yview_scroll(1, "units")
            else:
                self.text.yview_scroll(-1, "units")
        else:
            if event.num == 5:
                self.text.yview_scroll(1, "units")
            elif event.num == 4:
                self.text.yview_scroll(-1, "units")
        self.update_linenumbers()
        return "break"

    def _on_vscroll(self, *args):
        self.text.yview(*args)
        self.linenumbers.yview(*args)

    def _on_textscroll(self, *args):
        self.vbar.set(*args)
        try:
            self.linenumbers.yview_moveto(args[0])
        except Exception:
            pass

    def _on_textscroll_ln(self, *args):
        try:
            self.text.yview_moveto(args[0])
            self.vbar.set(*args)
        except Exception:
            pass

    def _setup_tags(self):
        font = ("Consolas", 11)
        self.text.configure(font=font, insertbackground="#ffffff", background="#1e1e1e",
                            foreground="#dcdcdc", selectbackground="#555555", wrap="none")
        self.linenumbers.configure(font=font)

        # Syntax tags
        self.text.tag_configure("kw", foreground="#ffb86c")
        self.text.tag_configure("builtin", foreground="#8be9fd")
        self.text.tag_configure("comment", foreground="#6272a4")
        self.text.tag_configure("string", foreground="#f1fa8c")
        self.text.tag_configure("number", foreground="#bd93f9")
        self.text.tag_configure("operator", foreground="#ff79c6")
        self.text.tag_configure("tag", foreground="#ffb86c")
        self.text.tag_configure("attr", foreground="#8be9fd")
        self.text.tag_configure("search", background="#444400")

    def schedule_update(self, delay=50):
        if self._update_ln_after_id:
            try:
                self.text.after_cancel(self._update_ln_after_id)
            except Exception:
                pass
        self._update_ln_after_id = self.text.after(delay, self.update_linenumbers)

    def on_text_change(self, event=None):
        self.schedule_update()

    def update_linenumbers(self):
        try:
            lines = int(self.text.index("end-1c").split(".")[0])
        except Exception:
            lines = 1
        self.linenumbers.config(state="normal")
        self.linenumbers.delete("1.0", "end")
        ln_txt = "\n".join(str(i) for i in range(1, lines + 1))
        self.linenumbers.insert("1.0", ln_txt)
        self.linenumbers.config(state="disabled")

    def on_key_release(self, event=None):
        # schedule syntax highlight after typing
        self.schedule_highlight()

    def schedule_highlight(self, delay=150):
        if self._highlight_after_id:
            try:
                self.text.after_cancel(self._highlight_after_id)
            except Exception:
                pass
        self._highlight_after_id = self.text.after(delay, self.highlight_syntax)

    def highlight_syntax(self):
        content = self.text.get("1.0", "end-1c")
        if content is None:
            return
        # clear tags
        for tag in ("kw", "builtin", "comment", "string", "number", "operator", "tag", "attr"):
            self.text.tag_remove(tag, "1.0", "end")
        if USE_PYGMENTS:
            try:
                self._highlight_with_pygments(content)
            except Exception:
                self._basic_highlight(content)
        else:
            self._basic_highlight(content)

    def _highlight_with_pygments(self, content):
        lexers = {
            "python": PythonLexer(),
            "c": CLexer(),
            "cpp": CppLexer(),
            "c++": CppLexer(),
            "html": HtmlLexer(),
            "htm": HtmlLexer(),
            "css": CssLexer(),
            "javascript": JavascriptLexer(),
            "js": JavascriptLexer(),
            "typescript": TypeScriptLexer(),
            "ts": TypeScriptLexer(),
            "rust": RustLexer(),
            "java": JavaLexer(),
            "lua": LuaLexer(),
            "go": GoLexer(),
            # Add new lexers
            "csharp": CSharpLexer(),
            "cs": CSharpLexer(),
            "ruby": RubyLexer(),
            "rb": RubyLexer(),
            "kotlin": KotlinLexer(),
            "kt": KotlinLexer(),
            "nix": NixLexer(),
        }
        lexer = lexers.get(self.language, PythonLexer())

        def map_token_to_tag(ttype):
            if ttype in Token.Comment or ttype in Token.Comment.Preproc:
                return "comment"
            if ttype in Token.Keyword:
                return "kw"
            if ttype in Token.Name.Builtin or ttype in Token.Name.Function or ttype in Token.Name.Class:
                return "builtin"
            if ttype in Token.String:
                return "string"
            if ttype in Token.Number:
                return "number"
            if ttype in Token.Operator or ttype in Token.Punctuation:
                return "operator"
            if ttype in Token.Name.Tag:
                return "tag"
            if ttype in Token.Name.Attribute:
                return "attr"
            return None

        pos = 0
        for tok_type, tok_str in lex(content, lexer):
            if not tok_str:
                continue
            length = len(tok_str)
            tag = map_token_to_tag(tok_type)
            if tag:
                try:
                    start = self._index_from_pos(pos, content)
                    end = self._index_from_pos(pos + length, content)
                    self.text.tag_add(tag, start, end)
                except Exception:
                    pass
            pos += length

    def _basic_highlight(self, content):
        lang = self.language
        # naive regex-based rules (keeps behavior if pygments absent)
        if lang in ("python",):
            for m in re.finditer(r"#.*", content):
                self._tag_range("comment", m.start(), m.end(), content)
            for m in re.finditer(r"(\"\"\".*?\"\"\"|'''.*?'''|\".*?\"|'.*?')", content, flags=re.S):
                self._tag_range("string", m.start(), m.end(), content)
            keywords = r"\b(?:def|class|if|else|elif|for|while|try|except|finally|with|as|import|from|return|in|is|and|or|not|lambda|pass|break|continue|yield|global|nonlocal|assert|del)\b"
            for m in re.finditer(keywords, content):
                self._tag_range("kw", m.start(), m.end(), content)
        elif lang in ("c", "cpp", "c++", "rust", "java", "go", "csharp", "cs", "kotlin", "kt"):  # Added C# and Kotlin
            for m in re.finditer(r"//.*", content):
                self._tag_range("comment", m.start(), m.end(), content)
            for m in re.finditer(r"/\*.*?\*/", content, flags=re.S):
                self._tag_range("comment", m.start(), m.end(), content)
            for m in re.finditer(r"\".*?\"|'.*?'", content, flags=re.S):
                self._tag_range("string", m.start(), m.end(), content)
            # Extended keywords for C# and Kotlin
            keywords = r"\b(?:int|char|float|double|void|if|else|for|while|do|switch|case|break|continue|return|struct|typedef|enum|const|static|extern|sizeof|class|public|private|protected|using|namespace|package|import|func|let|var|impl|trait|fn|match|mod|println|println!|string|bool|interface|virtual|override|sealed|abstract|readonly|async|await|fun|val|suspend|companion|object|init|constructor|internal|open|final|data|get|set)\b"
            for m in re.finditer(keywords, content):
                self._tag_range("kw", m.start(), m.end(), content)
        elif lang in ("ruby", "rb"):  # Add Ruby highlighting
            for m in re.finditer(r"#.*", content):
                self._tag_range("comment", m.start(), m.end(), content)
            for m in re.finditer(r"(\"\"\".*?\"\"\"|'''.*?'''|\".*?\"|'.*?'|%[qQ]?\{.*?\}|%[qQ]?\[.*?\])", content, flags=re.S):
                self._tag_range("string", m.start(), m.end(), content)
            keywords = r"\b(?:def|class|if|else|elsif|end|unless|case|when|while|until|for|in|do|module|begin|rescue|ensure|yield|return|super|self|nil|true|false|and|or|not|alias|undef|BEGIN|END)\b"
            for m in re.finditer(keywords, content):
                self._tag_range("kw", m.start(), m.end(), content)
        elif lang == "nix":  # Add Nix highlighting
            for m in re.finditer(r"#.*", content):
                self._tag_range("comment", m.start(), m.end(), content)
            for m in re.finditer(r"\".*?\"|''.*?''", content, flags=re.S):
                self._tag_range("string", m.start(), m.end(), content)
            keywords = r"\b(?:let|in|rec|with|inherit|or|import|importall|builtins|null|true|false|mkDerivation|mkShell|fetchFromGitHub|stdenv|lib|pkgs)\b"
            for m in re.finditer(keywords, content):
                self._tag_range("kw", m.start(), m.end(), content)
        elif lang in ("html", "htm"):
            for m in re.finditer(r"<[^>]+>", content):
                self._tag_range("tag", m.start(), m.end(), content)
            for m in re.finditer(r"(\w+)(?=\=)", content):
                self._tag_range("attr", m.start(), m.end(), content)
            for m in re.finditer(r"\".*?\"|'.*?'", content, flags=re.S):
                self._tag_range("string", m.start(), m.end(), content)
        elif lang in ("css",):
            for m in re.finditer(r"/\*.*?\*/", content, flags=re.S):
                self._tag_range("comment", m.start(), m.end(), content)
            for m in re.finditer(r"\".*?\"|'.*?'", content, flags=re.S):
                self._tag_range("string", m.start(), m.end(), content)
        elif lang in ("javascript", "js", "typescript", "ts", "lua"):
            for m in re.finditer(r"//.*", content):
                self._tag_range("comment", m.start(), m.end(), content)
            for m in re.finditer(r"/\*.*?\*/", content, flags=re.S):
                self._tag_range("comment", m.start(), m.end(), content)
            for m in re.finditer(r"\".*?\"|'.*?'|`.*?`", content, flags=re.S):
                self._tag_range("string", m.start(), m.end(), content)
            keywords = r"\b(?:function|var|let|const|if|else|for|while|return|new|this|class|extends|constructor|import|from|export|await|async|console|print)\b"
            for m in re.finditer(keywords, content):
                self._tag_range("kw", m.start(), m.end(), content)

    def _tag_range(self, tag, start_idx, end_idx, text):
        start = self._index_from_pos(start_idx, text)
        end = self._index_from_pos(end_idx, text)
        try:
            self.text.tag_add(tag, start, end)
        except Exception:
            pass

    def _index_from_pos(self, pos, text):
        upto = text[:pos]
        line = upto.count("\n") + 1
        if "\n" in upto:
            col = len(upto.split("\n")[-1])
        else:
            col = len(upto)
        return f"{line}.{col}"

    # ---------------------------
    # Auto-indent improvements
    # ---------------------------
    def on_return_key(self, event=None):
        """
        Auto-indent:
        - copy previous indent
        - if previous line ends with colon (python) or opener -> add indent
        - if cursor before closing brace, create block and position cursor
        """
        cur = self.text.index("insert")
        line, col = map(int, cur.split("."))
        prev_line_text = self.text.get(f"{max(1, line-1)}.0", f"{line-1}.end") if line > 1 else ""
        indent_match = re.match(r"^(\s*)", prev_line_text)
        indent = indent_match.group(1) if indent_match else ""
        extra = ""

        # languages where opener increases indent
        opener_increase = False
        prev_stripped = prev_line_text.rstrip()
        if self.language == "python" and prev_stripped.endswith(":"):
            opener_increase = True
        if prev_stripped.endswith("{"):
            opener_increase = True

        if opener_increase:
            extra = " " * 4

        # If immediate next non-space char is a closing brace, create a wrapped block
        rest_of_line = self.text.get(f"{line}.0", f"{line}.end")
        m = re.search(r"\S", rest_of_line)
        if m and rest_of_line[m.start()] in ("}", ")"):
            # insert newline indent + extra, newline indent, and put cursor at middle
            insert_text = "\n" + indent + extra + "\n" + indent
            self.text.insert("insert", insert_text)
            # set cursor on the indented middle line
            self.text.mark_set("insert", f"{line+1}.{len(indent + extra)}")
            self.schedule_highlight()
            return "break"

        # normal case
        self.text.insert("insert", "\n" + indent + extra)
        self.schedule_highlight()
        return "break"

    def on_tab_key(self, event=None):
        # indent selection or insert 4 spaces
        try:
            sel_start = self.text.index("sel.first")
            sel_end = self.text.index("sel.last")
            start_line = int(sel_start.split(".")[0])
            end_line = int(sel_end.split(".")[0])
            for ln in range(start_line, end_line + 1):
                self.text.insert(f"{ln}.0", " " * 4)
        except tk.TclError:
            self.text.insert("insert", " " * 4)
        return "break"

    def on_backspace(self, event=None):
        # smart dedent if previous 4 spaces present; also handle deleting paired empty quotes/brackets
        cur = self.text.index("insert")
        line, col = map(int, cur.split("."))
        if col >= 2:
            prev2 = self.text.get(f"{line}.{col-2}", f"{line}.{col}")
            if prev2 in ("()", "[]", "{}", "''", '""', "``"):
                # delete both
                self.text.delete(f"{line}.{col-2}", f"{line}.{col}")
                return "break"
        if col >= 4:
            prev4 = self.text.get(f"{line}.{col-4}", f"{line}.{col}")
            if prev4 == " " * 4:
                self.text.delete(f"{line}.{col-4}", f"{line}.{col}")
                return "break"
        return None

    # ---------------------------
    # Auto-pairs (insert pair on typing opener, wrap selection)
    # ---------------------------
    _PAIRS = {
        "(": ")",
        "[": "]",
        "{": "}",
        "\"": "\"",
        "'": "'",
        "`": "`",
    }

    def _on_keypress(self, event):
        # Handle only simple printable characters that are pairs
        ch = event.char
        if not ch:
            return  # control key
        if ch in self._PAIRS:
            # wrap selection if exists
            try:
                sel_start = self.text.index("sel.first")
                sel_end = self.text.index("sel.last")
                sel_text = self.text.get(sel_start, sel_end)
                # replace selection with wrapped selection
                closing = self._PAIRS[ch]
                self.text.delete(sel_start, sel_end)
                self.text.insert(sel_start, ch + sel_text + closing)
                # reselect inner text
                self.text.tag_remove("sel", "1.0", "end")
                self.text.tag_add("sel", sel_start + "+1c", f"{sel_start}+{1+len(sel_text)}c")
                self.text.mark_set("insert", f"{sel_start}+{1+len(sel_text)}c")
                self.schedule_highlight()
                return "break"
            except tk.TclError:
                # no selection -> insert pair and move cursor between them
                closing = self._PAIRS[ch]
                self.text.insert("insert", ch + closing)
                # move cursor backward one char to be between
                self.text.mark_set("insert", "insert-1c")
                self.schedule_highlight()
                return "break"
        # If user types closing char and it's already present right after cursor, skip insertion and move cursor right
        if ch in self._PAIRS.values():
            nxt = self.text.get("insert", "insert+1c")
            if nxt == ch:
                # consume the typed char by moving cursor over existing
                self.text.mark_set("insert", "insert+1c")
                return "break"
        # not handled -> allow default
        return None

    # ---------------------------
    # Comment toggle
    # ---------------------------
    def toggle_comment(self, event=None):
        try:
            sel_start = self.text.index("sel.first")
            sel_end = self.text.index("sel.last")
        except tk.TclError:
            cur = self.text.index("insert")
            sel_start = f"{cur.split('.')[0]}.0"
            sel_end = f"{cur.split('.')[0]}.end"
        start_line = int(sel_start.split(".")[0])
        end_line = int(sel_end.split(".")[0])
        if self.language == "python":
            prefix = "# "
        else:
            prefix = "// "
        lines = [self.text.get(f"{ln}.0", f"{ln}.end") for ln in range(start_line, end_line + 1)]
        if all(l.lstrip().startswith(prefix.strip()) for l in lines if l.strip() != ""):
            for i, ln in enumerate(range(start_line, end_line + 1)):
                line_text = lines[i]
                if prefix.strip() in line_text:
                    real_idx = self.text.search(re.escape(prefix.strip()), f"{ln}.0", f"{ln}.end", regexp=True)
                    if real_idx:
                        self.text.delete(real_idx, f"{real_idx}+{len(prefix.strip())}c")
        else:
            for ln in range(start_line, end_line + 1):
                self.text.insert(f"{ln}.0", prefix)
        self.schedule_highlight()
        return "break"

    def _sample_for_language(self):
        samples = {
            "python": "# New Python file\n\nif __name__ == '__main__':\n    print('Hello from Vanilla Studio')\n",
            "c": "/* New C file */\n#include <stdio.h>\n\nint main() {\n    printf(\"Hello, C from Vanilla Studio!\\n\");\n    return 0;\n}\n",
            "cpp": "// New C++ file\n#include <iostream>\nusing namespace std;\nint main(){\n    cout << \"Hello, C++ from Vanilla Studio!\" << endl;\n    return 0;\n}\n",
            "html": "<!doctype html>\n<html>\n  <head><meta charset=\"utf-8\"><title>Vanilla Studio</title></head>\n  <body>\n    <h1>Hello from Vanilla Studio</h1>\n  </body>\n</html>\n",
            "css": "/* New CSS */\nbody { font-family: sans-serif; background: #fff; color: #111; }\n",
            "javascript": "// New JavaScript\nconsole.log('Hello from Vanilla Studio');\n",
            "typescript": "// New TypeScript\nconsole.log('Hello from Vanilla Studio');\n",
            "rust": "// New Rust\nfn main() {\n    println!(\"Hello from Vanilla Studio\");\n}\n",
            "java": "// New Java\npublic class Main {\n    public static void main(String[] args) {\n        System.out.println(\"Hello from Vanilla Studio\");\n    }\n}\n",
            "lua": "-- New Lua\nprint('Hello from Vanilla Studio')\n",
            "go": "// New Go\npackage main\nimport \"fmt\"\nfunc main() {\n    fmt.Println(\"Hello from Vanilla Studio\")\n}\n",
            "csharp": "// New C#\nusing System;\nclass Program {\n    static void Main() {\n        Console.WriteLine(\"Hello from Vanilla Studio\");\n    }\n}\n",
            "ruby": "# New Ruby\nputs 'Hello from Vanilla Studio'\n",
            "kotlin": "// New Kotlin\nfun main() {\n    println(\"Hello from Vanilla Studio\")\n}\n",
            "nix": "# New Nix\n{ pkgs ? import <nixpkgs> {} }:\n\nwith pkgs;\n\nmkShell {\n  buildInputs = [\n    # Add your dependencies here\n  ];\n}\n",
        }
        return samples.get(self.language, "")

    def get_content(self):
        return self.text.get("1.0", "end-1c")

    def save(self, path=None):
        if path:
            self.filepath = Path(path)
        if not self.filepath:
            raise ValueError("No filepath set")
        expected_ext = LANG_EXT.get(self.language)
        if expected_ext:
            if self.filepath.suffix == "":
                self.filepath = self.filepath.with_suffix(expected_ext)
            else:
                if self.filepath.suffix.lower() != expected_ext.lower():
                    self.filepath = self.filepath.with_suffix(expected_ext)
        data = self.get_content()
        with open(self.filepath, "w", encoding="utf-8") as f:
            f.write(data)
        self.title = self.filepath.name
        self._update_tab_title()

    def _update_tab_title(self):
        try:
            idx = self.notebook.index(self.frame)
            self.notebook.tab(idx, text=self.title)
        except Exception:
            pass

# ---------------------------
# Main App
# ---------------------------
class VanillaStudioApp:
    def __init__(self, root):
        self.root = root
        self.root.title(APP_TITLE)
        self.root.geometry("1100x720")
        self.workspace_path = None  # current workspace folder path (Path)
        self._setup_style()
        self._create_widgets()
        self._setup_menu()
        self._bind_shortcuts()
        self.new_file()
        if not USE_PYGMENTS:
            self.append_console("Note: Pygments not found. Install 'pygments' (pip install pygments) for improved highlighting.\n")

    def _setup_style(self):
        try:
            style = ttk.Style()
            style.theme_use("clam")
        except Exception:
            pass

    def _create_widgets(self):
        # Main PanedWindow: left for file tree (optional), right for editor
        self.main_pane = ttk.Panedwindow(self.root, orient="horizontal")
        self.main_pane.pack(fill="both", expand=True)

        # Left placeholder frame (will contain file tree when workspace opened)
        self.left_frame = ttk.Frame(self.main_pane, width=240)
        # Do not add now; add when workspace opened

        # Right frame contains toolbar, notebook, console
        self.right_outer = ttk.Frame(self.main_pane)
        self.main_pane.add(self.right_outer, weight=4)

        # Toolbar
        toolbar = ttk.Frame(self.right_outer)
        toolbar.pack(side="top", fill="x")

        new_btn = ttk.Button(toolbar, text="New", command=self.new_file)
        open_btn = ttk.Button(toolbar, text="Open", command=self.open_file)
        save_btn = ttk.Button(toolbar, text="Save", command=self.save_file)
        saveas_btn = ttk.Button(toolbar, text="Save As", command=self.save_file_as)
        run_btn = ttk.Button(toolbar, text="Run ▶", command=self.run_current)
        stop_btn = ttk.Button(toolbar, text="Stop ⛔", command=self.stop_current)
        close_btn = ttk.Button(toolbar, text="Close Tab ✕", command=self.close_current_tab)
        open_ws_btn = ttk.Button(toolbar, text="Open Workspace", command=self.open_workspace)
        close_ws_btn = ttk.Button(toolbar, text="Close Workspace", command=self.close_workspace)
        lang_label = ttk.Label(toolbar, text="Language:")
        self.lang_var = tk.StringVar(value="python")
        self.lang_box = ttk.Combobox(toolbar, textvariable=self.lang_var,
                                     values=["Python","C","C++","HTML","CSS","JavaScript",
                                             "TypeScript","Rust","Java","Lua","Go","C#",
                                             "Ruby","Kotlin","Nix"], width=12)
        new_btn.pack(side="left", padx=2, pady=2)
        open_btn.pack(side="left", padx=2, pady=2)
        save_btn.pack(side="left", padx=2, pady=2)
        saveas_btn.pack(side="left", padx=2, pady=2)
        run_btn.pack(side="left", padx=6, pady=2)
        stop_btn.pack(side="left", padx=2, pady=2)
        close_btn.pack(side="left", padx=2, pady=2)
        open_ws_btn.pack(side="left", padx=8)
        close_ws_btn.pack(side="left", padx=2)
        lang_label.pack(side="left", padx=(10,2))
        self.lang_box.pack(side="left", padx=2)

        # Notebook (tabs)
        self.notebook = ttk.Notebook(self.right_outer)
        self.notebook.pack(fill="both", expand=True)
        self.tabs = []

        # Bottom console
        console_frame = ttk.Frame(self.right_outer)
        console_frame.pack(side="bottom", fill="x")
        console_label = ttk.Label(console_frame, text="Console Output:")
        console_label.pack(anchor="w")
        self.console = ScrolledText(console_frame, height=10, state="disabled")
        self.console.pack(fill="both", expand=False)

        # Status bar
        self.status_var = tk.StringVar(value="Ready")
        status = ttk.Label(self.root, textvariable=self.status_var, relief="sunken", anchor="w")
        status.pack(side="bottom", fill="x")

        # Running state
        self._run_thread = None
        self._stop_event = threading.Event()

        # File tree (Treeview) - created but not packed until workspace opened
        self.tree = ttk.Treeview(self.left_frame, columns=("fullpath", "type"), displaycolumns=())
        self.tree.heading("#0", text="Workspace", anchor="w")
        self.tree.bind("<Double-1>", self._on_tree_double_click)
        # Add a small close workspace button
        self.left_top = ttk.Frame(self.left_frame)
        self.left_top.pack(side="top", fill="x")
        self.ws_label_var = tk.StringVar(value="Workspace: (none)")
        self.ws_label = ttk.Label(self.left_top, textvariable=self.ws_label_var)
        self.ws_label.pack(side="left", padx=4, pady=4)
        self.close_ws_btn_small = ttk.Button(self.left_top, text="Close", command=self.close_workspace)
        self.close_ws_btn_small.pack(side="right", padx=4, pady=4)

    def _setup_menu(self):
        menubar = tk.Menu(self.root)
        filemenu = tk.Menu(menubar, tearoff=0)
        filemenu.add_command(label="New", command=self.new_file, accelerator="Ctrl+N")
        filemenu.add_command(label="Open...", command=self.open_file, accelerator="Ctrl+O")
        filemenu.add_command(label="Save", command=self.save_file, accelerator="Ctrl+S")
        filemenu.add_command(label="Save As...", command=self.save_file_as, accelerator="Ctrl+Shift+S")
        filemenu.add_separator()
        filemenu.add_command(label="Exit", command=self.root.quit)
        menubar.add_cascade(label="File", menu=filemenu)

        editmenu = tk.Menu(menubar, tearoff=0)
        editmenu.add_command(label="Undo", command=self._current_text_event("edit_undo"), accelerator="Ctrl+Z")
        editmenu.add_command(label="Redo", command=self._current_text_event("edit_redo"), accelerator="Ctrl+Y")
        editmenu.add_separator()
        editmenu.add_command(label="Find", command=self.find_text, accelerator="Ctrl+F")
        editmenu.add_command(label="Close Tab", command=self.close_current_tab, accelerator="Ctrl+W")
        menubar.add_cascade(label="Edit", menu=editmenu)

        runmenu = tk.Menu(menubar, tearoff=0)
        runmenu.add_command(label="Run", command=self.run_current, accelerator="F5")
        menubar.add_cascade(label="Run", menu=runmenu)

        helpmenu = tk.Menu(menubar, tearoff=0)
        helpmenu.add_command(label="About", command=self.show_about)
        menubar.add_cascade(label="Help", menu=helpmenu)

        self.root.config(menu=menubar)

    def _bind_shortcuts(self):
        self.root.bind_all("<Control-n>", lambda e: self.new_file())
        self.root.bind_all("<Control-o>", lambda e: self.open_file())
        self.root.bind_all("<Control-s>", lambda e: self.save_file())
        self.root.bind_all("<Control-S>", lambda e: self.save_file_as())
        self.root.bind_all("<F5>", lambda e: self.run_current())
        self.root.bind_all("<Control-f>", lambda e: self.find_text())
        self.root.bind_all("<Control-w>", lambda e: self.close_current_tab())

    def _current_text_event(self, cmd):
        def _do():
            tab = self.get_current_tab()
            if not tab:
                return
            try:
                getattr(tab.text, cmd)()
            except Exception:
                pass
        return _do

    # ---------------------------
    # Tabs & files
    # ---------------------------
    def new_file(self):
        language = self.lang_var.get()
        title = "untitled" + (LANG_EXT.get(language, "") or "")
        tab = EditorTab(self.notebook, self, title=title, filepath=None, language=language)
        self.tabs.append(tab)
        self.notebook.add(tab.frame, text=tab.title)
        self.notebook.select(tab.frame)
        self.update_status("New file")

    def open_file(self, path=None):
        if not path:
            path = filedialog.askopenfilename(filetypes=[("All files","*.*"), ("Python","*.py"),("C files","*.c"),("C++ files","*.cpp;*.cxx;*.cc"),("HTML","*.html;*.htm"),("CSS","*.css"),("JavaScript","*.js"),("TypeScript",".ts"),("Rust",".rs"),("Java",".java"),("Lua",".lua"),("Go",".go")])
        if not path:
            return
        path = Path(path)
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = f.read()
        except Exception as e:
            messagebox.showerror("Open file", f"Unable to open file: {e}")
            return
        ext = path.suffix.lower().lstrip(".")
        lang_map = {
            "py":"python", "c":"c", "cpp":"cpp", "cc":"cpp", "cxx":"cpp",
            "html":"html", "htm":"html", "css":"css", "js":"javascript",
            "ts":"typescript", "rs":"rust", "java":"java", "lua":"lua", "go":"go"
        }
        language = lang_map.get(ext, "python")
        tab = EditorTab(self.notebook, self, title=path.name, filepath=path, language=language)
        tab.text.delete("1.0", "end")
        tab.text.insert("1.0", data)
        tab.schedule_highlight()
        tab.update_linenumbers()
        self.tabs.append(tab)
        self.notebook.add(tab.frame, text=tab.title)
        self.notebook.select(tab.frame)
        self.update_status(f"Opened {path}")

    def get_current_tab(self):
        sel = self.notebook.select()
        for tab in self.tabs:
            if str(tab.frame) == sel:
                return tab
        return None

    def save_file(self):
        tab = self.get_current_tab()
        if not tab:
            return
        if not tab.filepath:
            return self.save_file_as()
        try:
            tab.save()
            self.update_status(f"Saved {tab.filepath}")
            self.append_console(f"Saved: {tab.filepath}\n")
        except Exception as e:
            messagebox.showerror("Save", f"Error saving file: {e}")

    def save_file_as(self):
        tab = self.get_current_tab()
        if not tab:
            return
        suggested = "untitled" + (LANG_EXT.get(tab.language, ""))
        path = filedialog.asksaveasfilename(defaultextension=LANG_EXT.get(tab.language, ""), initialfile=suggested, filetypes=[("All files","*.*")])
        if not path:
            return
        path = Path(path)
        expected_ext = LANG_EXT.get(tab.language)
        if expected_ext:
            if path.suffix == "":
                path = path.with_suffix(expected_ext)
            elif path.suffix.lower() != expected_ext.lower():
                path = path.with_suffix(expected_ext)
        try:
            tab.save(str(path))
            self.update_status(f"Saved {path}")
            self.append_console(f"Saved as: {path}\n")
        except Exception as e:
            messagebox.showerror("Save As", f"Error saving file: {e}")

    def close_current_tab(self):
        tab = self.get_current_tab()
        if not tab:
            return
        if not tab.filepath:
            res = messagebox.askyesno("Close Tab", "This tab is unsaved. Close anyway?")
            if not res:
                return
        try:
            idx = self.notebook.index(tab.frame)
            self.notebook.forget(idx)
            self.tabs = [t for t in self.tabs if t is not tab]
            try:
                tab.frame.destroy()
            except Exception:
                pass
            self.update_status("Tab closed")
        except Exception:
            pass

    # ---------------------------
    # Workspace (file tree)
    # ---------------------------
    def open_workspace(self):
        folder = filedialog.askdirectory()
        if not folder:
            return
        self.workspace_path = Path(folder)
        # if left_frame not in pane, add it
        try:
            # remove old left if present
            existing = [p for p in self.main_pane.panes()]
            # add left_frame at index 0
            if str(self.left_frame) not in existing:
                self.main_pane.insert(0, self.left_frame)
        except Exception:
            try:
                self.main_pane.add(self.left_frame, weight=1)
            except Exception:
                pass

        # clear previous tree
        for i in self.tree.get_children():
            self.tree.delete(i)
        # populate tree (limited depth)
        self._populate_tree(self.workspace_path, "")
        self.tree.pack(fill="both", expand=True)
        self.ws_label_var.set(f"Workspace: {self.workspace_path.name}")

    def _populate_tree(self, root_path: Path, parent_node, max_depth=3, current_depth=0):
        """Recursively add files and directories to tree (limited depth)."""
        if current_depth > max_depth:
            return
        try:
            entries = sorted(root_path.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower()))
        except PermissionError:
            return
        for p in entries:
            node_text = p.name + ("/" if p.is_dir() else "")
            node_id = self.tree.insert(parent_node, "end", text=node_text, values=(str(p), "dir" if p.is_dir() else "file"))
            if p.is_dir():
                # insert a dummy child so folder shows as expandable
                try:
                    # populate one level deep for quicker browsing
                    self._populate_tree(p, node_id, max_depth, current_depth + 1)
                except Exception:
                    pass

    def _on_tree_double_click(self, event):
        item = self.tree.selection()
        if not item:
            return
        it = item[0]
        full = self.tree.set(it, "fullpath")
        if not full:
            # fallback: text value
            txt = self.tree.item(it, "text")
            full = str(self.workspace_path / txt)
        path = Path(full)
        if path.is_dir():
            # toggle expand/collapse
            if self.tree.get_children(it):
                # already expanded -> collapse
                if self.tree.item(it, "open"):
                    self.tree.item(it, open=False)
                else:
                    self.tree.item(it, open=True)
            else:
                # populate children
                self._populate_tree(path, it)
                self.tree.item(it, open=True)
        else:
            self.open_file(str(path))

    def close_workspace(self):
        if self.workspace_path is None:
            return
        # remove left_frame from main_pane
        try:
            self.main_pane.forget(self.left_frame)
        except Exception:
            try:
                self.main_pane.remove(self.left_frame)
            except Exception:
                pass
        self.workspace_path = None
        self.tree.pack_forget()
        self.ws_label_var.set("Workspace: (none)")

    # ---------------------------
    # Running code
    # ---------------------------
    def run_current(self):
        tab = self.get_current_tab()
        if not tab:
            return
        # Ensure saved
        if not tab.filepath:
            res = messagebox.askyesno("Save", "File must be saved before running. Save now?")
            if res:
                self.save_file_as()
            else:
                return
        if not tab.filepath:
            return
        lang = tab.language
        self.append_console(f"Running {tab.title} ({lang})...\n")
        self.update_status("Running...")
        self._stop_event.clear()

        def runner():
            try:
                if lang == "python":
                    cmd = [sys.executable, str(tab.filepath)]
                    code, out, err = safe_run_subprocess(cmd, cwd=str(tab.filepath.parent))
                    if out:
                        self.append_console(out)
                    if err:
                        self.append_console(err)
                elif lang in ("c", "cpp", "c++"):
                    compiler = "gcc" if lang == "c" else "g++"
                    if not which(compiler):
                        self.append_console(f"Compiler '{compiler}' not found in PATH.\n")
                        return
                    exe_path = tab.filepath.with_suffix(".out")
                    cmd = [compiler, str(tab.filepath), "-o", str(exe_path)]
                    code, out, err = safe_run_subprocess(cmd, cwd=str(tab.filepath.parent))
                    if code != 0:
                        if out: self.append_console(out)
                        if err: self.append_console(err)
                        return
                    code, out, err = safe_run_subprocess([str(exe_path)], cwd=str(tab.filepath.parent))
                    if out: self.append_console(out)
                    if err: self.append_console(err)
                elif lang == "rust":
                    # try rustc
                    if which("rustc"):
                        exe = tab.filepath.with_suffix("")
                        exe = exe.with_suffix(".out")
                        cmd = ["rustc", str(tab.filepath), "-o", str(exe)]
                        code, out, err = safe_run_subprocess(cmd, cwd=str(tab.filepath.parent))
                        if code != 0:
                            if out: self.append_console(out)
                            if err: self.append_console(err)
                            return
                        code, out, err = safe_run_subprocess([str(exe)], cwd=str(tab.filepath.parent))
                        if out: self.append_console(out)
                        if err: self.append_console(err)
                    else:
                        self.append_console("rustc not found in PATH.\n")
                elif lang == "go":
                    if which("go"):
                        cmd = ["go", "run", str(tab.filepath)]
                        code, out, err = safe_run_subprocess(cmd, cwd=str(tab.filepath.parent))
                        if out: self.append_console(out)
                        if err: self.append_console(err)
                    else:
                        self.append_console("go not found in PATH.\n")
                elif lang == "java":
                    if which("javac") and which("java"):
                        classdir = tab.filepath.parent
                        code, out, err = safe_run_subprocess(["javac", str(tab.filepath)], cwd=str(classdir))
                        if code != 0:
                            if out: self.append_console(out)
                            if err: self.append_console(err)
                            return
                        # find classname (use filename)
                        classname = tab.filepath.stem
                        code, out, err = safe_run_subprocess(["java", classname], cwd=str(classdir))
                        if out: self.append_console(out)
                        if err: self.append_console(err)
                    else:
                        self.append_console("javac/java not found in PATH.\n")
                elif lang in ("javascript", "js"):
                    if which("node"):
                        code, out, err = safe_run_subprocess(["node", str(tab.filepath)], cwd=str(tab.filepath.parent))
                        if out: self.append_console(out)
                        if err: self.append_console(err)
                    else:
                        webbrowser.open(str(tab.filepath.resolve().as_uri()))
                        self.append_console("Node not found — opened file in browser instead.\n")
                elif lang in ("typescript", "ts"):
                    # tsc -> compile to js then node
                    if which("tsc"):
                        js_out = tab.filepath.with_suffix(".js")
                        code, out, err = safe_run_subprocess(["tsc", str(tab.filepath), "--outFile", str(js_out)], cwd=str(tab.filepath.parent))
                        if code != 0:
                            if out: self.append_console(out)
                            if err: self.append_console(err)
                            return
                        if which("node"):
                            code, out, err = safe_run_subprocess(["node", str(js_out)], cwd=str(tab.filepath.parent))
                            if out: self.append_console(out)
                            if err: self.append_console(err)
                        else:
                            webbrowser.open(str(js_out.resolve().as_uri()))
                            self.append_console("Node not found — opened compiled JS in browser.\n")
                    else:
                        self.append_console("tsc (TypeScript compiler) not found in PATH.\n")
                elif lang == "lua":
                    if which("lua"):
                        code, out, err = safe_run_subprocess(["lua", str(tab.filepath)], cwd=str(tab.filepath.parent))
                        if out: self.append_console(out)
                        if err: self.append_console(err)
                    else:
                        self.append_console("lua not found in PATH.\n")
                elif lang in ("html", "htm", "css"):
                    webbrowser.open(str(tab.filepath.resolve().as_uri()))
                    self.append_console(f"Opened {tab.filepath} in default browser.\n")
                elif lang == "csharp":
                    if which("dotnet"):
                        # Create temporary project if needed
                        proj_dir = tab.filepath.parent
                        if not (proj_dir / "project.csproj").exists():
                            code, out, err = safe_run_subprocess(["dotnet", "new", "console", "-o", "."], cwd=str(proj_dir))
                        code, out, err = safe_run_subprocess(["dotnet", "run"], cwd=str(proj_dir))
                        if out: self.append_console(out)
                        if err: self.append_console(err)
                    else:
                        self.append_console(".NET SDK not found in PATH.\n")
                elif lang == "ruby":
                    if which("ruby"):
                        code, out, err = safe_run_subprocess(["ruby", str(tab.filepath)], cwd=str(tab.filepath.parent))
                        if out: self.append_console(out)
                        if err: self.append_console(err)
                    else:
                        self.append_console("Ruby not found in PATH.\n")
                elif lang == "kotlin":
                    if which("kotlinc"):
                        # Compile and run using kotlinc
                        code, out, err = safe_run_subprocess(["kotlinc", str(tab.filepath), "-include-runtime", "-d", "out.jar"], 
                                                           cwd=str(tab.filepath.parent))
                        if code != 0:
                            if out: self.append_console(out)
                            if err: self.append_console(err)
                            return
                        code, out, err = safe_run_subprocess(["java", "-jar", "out.jar"], cwd=str(tab.filepath.parent))
                        if out: self.append_console(out)
                        if err: self.append_console(err)
                    else:
                        self.append_console("Kotlin compiler not found in PATH.\n")
                elif lang == "nix":
                    if which("nix"):
                        if tab.filepath.name == "flake.nix":
                            code, out, err = safe_run_subprocess(["nix", "develop", "."], cwd=str(tab.filepath.parent))
                        else:
                            code, out, err = safe_run_subprocess(["nix-shell", str(tab.filepath)], cwd=str(tab.filepath.parent))
                        if out: self.append_console(out)
                        if err: self.append_console(err)
                    else:
                        self.append_console("Nix not found in PATH.\n")
                else:
                    self.append_console("Unknown language: cannot run.\n")
            except Exception as e:
                self.append_console(f"Error: {e}\n")
            finally:
                self.update_status("Ready")

        self._run_thread = threading.Thread(target=runner, daemon=True)
        self._run_thread.start()

    def stop_current(self):
        self._stop_event.set()
        self.append_console("Stop requested (best-effort). If an external process started, stop it manually.\n")
        self.update_status("Stopped")

    # ---------------------------
    # UI helpers
    # ---------------------------
    def append_console(self, text):
        self.console.config(state="normal")
        self.console.insert("end", text)
        self.console.see("end")
        self.console.config(state="disabled")

    def update_status(self, text):
        self.status_var.set(text)

    def find_text(self):
        tab = self.get_current_tab()
        if not tab:
            return
        needle = simpledialog.askstring("Find", "Text to find:")
        if not needle:
            return
        start = tab.text.search(needle, "1.0", stopindex="end")
        if not start:
            messagebox.showinfo("Find", "Not found.")
            return
        tab.text.tag_remove("search", "1.0", "end")
        tab.text.tag_configure("search", background="#444400")
        end = f"{start}+{len(needle)}c"
        tab.text.tag_add("search", start, end)
        tab.text.mark_set("insert", end)
        tab.text.see(start)

    def show_about(self):
        messagebox.showinfo("About Vanilla Studio", "Vanilla Studio IDE\nA beginner-friendly IDE\nSupports: Python, C, C++, HTML, CSS, JavaScript, TypeScript, Rust, Java, Lua, Go\nCreated with ♥ by Camila Rose")

# ---------------------------
# Run application
# ---------------------------
def main():
    root = tk.Tk()
    app = VanillaStudioApp(root)
    root.mainloop()

if __name__ == "__main__":
    main()
