# -*- coding: utf-8 -*-
"""Fwords 独立计时器（单文件桌面版）

从背单词 App 里把「计时」这一件事剥出来，做成一个双击就能用的桌面计时器：
  · 净学习时长正计时（唯一时间轴）
  · T2 倒计时（默认每 5 分钟）
  · T3 倒计时（默认每 30 分钟）
  · 到点只提示、不打断（跟 App 里的软触发一致）
  · 暂停 / 重置 / 窗口置顶 / 状态自动保存

跟 demo/app.js 保持一致的几条约定（改动前先想清楚）：
  · 时间轴用「净学习时长」，不用墙上时钟。
  · 计时用时间差累加（perf_counter 进出差值），不用计数器 —— 后者在卡顿/切后台时漂移。
  · 单次 tick 间隔 > IDLE_GAP_MS 视为切后台/卡顿，这段时间不计入（防止「切走 10 分钟回来发现早该回顾了」）。
  · T3 到点时同时复位 T2（T3 窗口在集合意义上包含 T2，否则会立刻重复弹一次）。
"""
import json
import os
import sys
import time
import tkinter as tk
from tkinter import font as tkfont

APP_TITLE = 'Fwords 计时器'
STATE_FILE = 'timer-state.json'

TICK_MS = 200           # UI 刷新间隔
IDLE_GAP_MS = 700# 单次 tick 间隔超过此值 → 判定切后台/卡顿，不计入

DEFAULT_T2_MIN = 5
DEFAULT_T3_MIN = 30

# 配色（浅色）
BG = '#f5f6f8'
CARD = '#ffffff'
TEXT = '#1f2430'
MUTED = '#8b93a3'
ACCENT = '#2f6df6'
TRACK = '#e8eaef'
OK = '#12b76a'
WARN = '#f79009'
DANGER = '#e5484d'
BANNER_BG = '#fff7e6'
BANNER_FG = '#b54708'
BTN_BG = '#eef1f6'


# ==================== 小工具 ====================
def data_dir():
    """状态文件放哪：优先 exe 同级目录（便携），不可写则退回 %APPDATA%。"""
    if getattr(sys, 'frozen', False):
        base = os.path.dirname(sys.executable)
    else:
        base = os.path.dirname(os.path.abspath(__file__))
    try:
        p = os.path.join(base, '.' + STATE_FILE + '.probe')
        with open(p, 'w') as f:
            f.write('1')
        os.remove(p)
        return base
    except Exception:
        import tempfile
        d = os.path.join(os.environ.get('APPDATA', tempfile.gettempdir()), 'FwordsTimer')
        os.makedirs(d, exist_ok=True)
        return d


def state_path():
    return os.path.join(data_dir(), STATE_FILE)


def load_state():
    try:
        with open(state_path(), 'r', encoding='utf-8') as f:
            obj = json.load(f)
        if isinstance(obj, dict):
            return obj
    except Exception:
        pass
    return {}


def save_state(obj):
    try:
        tmp = state_path() + '.tmp'
        with open(tmp, 'w', encoding='utf-8') as f:
            json.dump(obj, f, ensure_ascii=False, indent=2)
        os.replace(tmp, state_path())
    except Exception:
        pass


def fmt(ms):
    """毫秒 → mm:ss（超过 1 小时显示 h:mm:ss）。"""
    s = max(0, int(ms // 1000))
    h, m, s = s // 3600, (s % 3600) // 60, s % 60
    if h:
        return '%d:%02d:%02d' % (h, m, s)
    return '%02d:%02d' % (m, s)


def beep(root):
    try:
        import winsound
        winsound.Beep(880, 260)
        winsound.Beep(1180, 260)
    except Exception:
        try:
            root.bell()
        except Exception:
            pass


# ==================== 主界面 ====================
class TimerApp(object):
    def __init__(self, root):
        self.root = root
        root.title(APP_TITLE)
        root.configure(bg=BG)
        root.resizable(False, False)

        st = load_state()
        self.running = bool(st.get('running', False))
        self.net_ms = float(st.get('netMs', 0))
        self.t2_ms = int(st.get('t2Ms', DEFAULT_T2_MIN)) * 60 * 1000
        self.t3_ms = int(st.get('t3Ms', DEFAULT_T3_MIN)) * 60 * 1000
        self.t2_el = float(st.get('t2Elapsed', 0)) % max(1, self.t2_ms)
        self.t3_el = float(st.get('t3Elapsed', 0)) % max(1, self.t3_ms)
        self.pause_on_due = bool(st.get('pauseOnDue', False))
        self.sound = bool(st.get('sound', True))
        self.topmost = bool(st.get('topmost', True))

        self.last_tick = time.perf_counter()
        self.dirty = False
        self._last_save = 0.0

        self._build()
        self._sync_controls()
        self._apply_topmost()
        self.root.protocol('WM_DELETE_WINDOW', self.on_close)
        self.root.bind('<space>', lambda e: self.toggle())
        self.root.bind('<Key-r>', lambda e: self.reset())
        self.root.bind('<Key-R>', lambda e: self.reset())
        self.render()
        self.root.after(TICK_MS, self.tick)

    # ---------- 界面 ----------
    def _build(self):
        f = tkfont.nametofont('TkDefaultFont')
        try:
            f.configure(family='Microsoft YaHei UI', size=10)
            self.root.option_add('*Font', f)
        except Exception:
            pass

        pad = 24
        wrap = tk.Frame(self.root, bg=BG)
        wrap.pack(padx=pad, pady=(18, 14))

        # 标题行
        head = tk.Frame(wrap, bg=BG)
        head.pack(fill='x')
        tk.Label(head, text=APP_TITLE, bg=BG, fg=TEXT,
                 font=('Microsoft YaHei UI', 13, 'bold')).pack(side='left')
        self.dot = tk.Label(head, text='', bg=BG, fg=OK, font=('Consolas', 11, 'bold'))
        self.dot.pack(side='right')

        # 大数字
        self.big = tk.Label(wrap, text='00:00', bg=BG, fg=TEXT,
                            font=('Consolas', 58, 'bold'))
        self.big.pack(pady=(6, 0))
        tk.Label(wrap, text='净学习时长 · 暂停与离开窗口都不计入', bg=BG, fg=MUTED,
                 font=('Microsoft YaHei UI', 9)).pack()

        # 两个倒计时环
        rings = tk.Frame(wrap, bg=BG)
        rings.pack(pady=(16, 0))
        self.ring_t2 = self._make_ring(rings, 'T2 · 每 %d 分钟' % (self.t2_ms // 60000), ACCENT)
        self.ring_t3 = self._make_ring(rings, 'T3 · 每 %d 分钟' % (self.t3_ms // 60000), OK)

        # 提示条容器（常驻占位，内部提示条按需显示/隐藏，避免布局跳动）
        self.banner_slot = tk.Frame(wrap, bg=BG)
        self.banner_slot.pack(fill='x', pady=(14, 0))
        self.banner = tk.Label(self.banner_slot, text='', bg=BANNER_BG, fg=BANNER_FG,
                               font=('Microsoft YaHei UI', 10, 'bold'),
                               padx=14, pady=9)

        # 按钮
        row = tk.Frame(wrap, bg=BG)
        row.pack(pady=(16, 0))
        self.btn_run = self._btn(row, '开始', self.toggle, width=11)
        self.btn_run.pack(side='left', padx=(0, 8))
        self._btn(row, '重置', self.reset, width=9).pack(side='left', padx=(0, 8))
        self.top_var = tk.BooleanVar(value=self.topmost)
        tk.Checkbutton(row, text='窗口置顶', variable=self.top_var, bg=BG, fg=TEXT,
                       activebackground=BG, activeforeground=TEXT, selectcolor=BG,
                       bd=0, highlightthickness=0, font=('Microsoft YaHei UI', 10),
                       command=self._on_topmost).pack(side='left')

        # 设置
        line = tk.Frame(wrap, bg=BG)
        line.pack(pady=(12, 0))
        tk.Label(line, text='T2 周期', bg=BG, fg=MUTED, font=('Microsoft YaHei UI', 9)).pack(side='left')
        self.sp_t2 = tk.Spinbox(line, from_=1, to=180, width=4, bd=0,
                                highlightthickness=1, justify='center',
                                font=('Consolas', 11), command=self._on_period)
        self.sp_t2.pack(side='left', padx=(4, 14))
        tk.Label(line, text='分钟', bg=BG, fg=MUTED, font=('Microsoft YaHei UI', 9)).pack(side='left')

        tk.Label(line, text='T3 周期', bg=BG, fg=MUTED, font=('Microsoft YaHei UI', 9)).pack(side='left', padx=(0, 4))
        self.sp_t3 = tk.Spinbox(line, from_=1, to=600, width=4, bd=0,
                                highlightthickness=1, justify='center',
                                font=('Consolas', 11), command=self._on_period)
        self.sp_t3.pack(side='left', padx=(0, 4))
        tk.Label(line, text='分钟', bg=BG, fg=MUTED, font=('Microsoft YaHei UI', 9)).pack(side='left', padx=(0, 14))

        self.sound_var = tk.BooleanVar(value=self.sound)
        tk.Checkbutton(line, text='到点响铃', variable=self.sound_var, bg=BG, fg=TEXT,
                       activebackground=BG, activeforeground=TEXT, selectcolor=BG,
                       bd=0, highlightthickness=0, font=('Microsoft YaHei UI', 9),
                       command=self._on_flag).pack(side='left')

        line2 = tk.Frame(wrap, bg=BG)
        line2.pack(pady=(2, 0))
        self.pause_var = tk.BooleanVar(value=self.pause_on_due)
        tk.Checkbutton(line2, text='到点后自动暂停，等我确认', variable=self.pause_var,
                       bg=BG, fg=TEXT, activebackground=BG, activeforeground=TEXT,
                       selectcolor=BG, bd=0, highlightthickness=0,
                       font=('Microsoft YaHei UI', 9),
                       command=self._on_flag).pack(side='left')

        tk.Label(wrap, text='空格 = 开始/暂停　R = 重置　关闭窗口后进度会保留',
                 bg=BG, fg=MUTED, font=('Microsoft YaHei UI', 8)).pack(pady=(12, 0))

        self.banner_text = ''
        self._draw_rings()

    def _btn(self, parent, text, cmd, width=9):
        b = tk.Button(parent, text=text, command=cmd, width=width,
                      bg=BTN_BG, fg=TEXT, activebackground='#e2e7ef',
                      activeforeground=TEXT, bd=0, highlightthickness=0,
                      font=('Microsoft YaHei UI', 10), cursor='hand2', pady=6)
        return b

    def _make_ring(self, parent, label, color):
        box = tk.Frame(parent, bg=BG, padx=18)
        box.pack(side='left')
        cv = tk.Canvas(box, width=150, height=150, bg=BG, highlightthickness=0)
        cv.pack()
        self._rings = getattr(self, '_rings', [])
        lab = tk.Label(box, text=label, bg=BG, fg=MUTED,
                       font=('Microsoft YaHei UI', 9))
        lab.pack(pady=(2, 0))
        item = {'canvas': cv, 'color': color, 'label': lab}
        self._rings.append(item)
        return item

    # ---------- 绘制 ----------
    def _draw_rings(self):
        for r in self._rings:
            cv = r['canvas']
            cv.delete('all')
            pad, w = 12, 13
            box = (pad, pad, 150 - pad, 150 - pad)
            cv.create_arc(box[0], box[1], box[2], box[3], start=135, extent=270,
                          style='arc', width=w, outline=TRACK)

    def _paint_ring(self, item, remain_ms, total_ms, due):
        cv = item['canvas']
        cv.delete('arc')
        cv.delete('txt')
        pad, w = 12, 13
        box = (pad, pad, 150 - pad, 150 - pad)
        frac = 0.0 if total_ms <= 0 else max(0.0, min(1.0, remain_ms / float(total_ms)))
        color = DANGER if due else item['color']
        if frac > 0:
            cv.create_arc(box[0], box[1], box[2], box[3], start=135,
                          extent=270 * frac, style='arc', width=w, outline=color)
        cv.create_text(75, 71, text=fmt(remain_ms), fill=TEXT if not due else DANGER,
                       font=('Consolas', 26, 'bold'), tags='txt')
        cv.create_text(75, 96, text='剩余', fill=MUTED,
                       font=('Microsoft YaHei UI', 9), tags='txt')

    def render(self):
        self.big.configure(text=fmt(self.net_ms))
        self.big.configure(fg=TEXT if self.running else MUTED)
        self.dot.configure(text='● 运行中' if self.running else '‖ 已暂停',
                           fg=OK if self.running else MUTED)
        self.btn_run.configure(text='暂停' if self.running else '开始')

        r2 = max(0, self.t2_ms - self.t2_el)
        r3 = max(0, self.t3_ms - self.t3_el)
        self._paint_ring(self.ring_t2, r2, self.t2_ms, r2 <= 0)
        self._paint_ring(self.ring_t3, r3, self.t3_ms, r3 <= 0)

        if self.banner_text:
            self.banner.configure(text=self.banner_text)
            if not self.banner.winfo_ismapped():
                self.banner.pack(fill='x')
        else:
            if self.banner.winfo_ismapped():
                self.banner.pack_forget()

    # ---------- 主循环 ----------
    def tick(self):
        now = time.perf_counter()
        delta = (now - self.last_tick) * 1000.0
        self.last_tick = now
        # 间隔异常 → 窗口被切走或主线程卡死，这段时间不计入
        if delta > IDLE_GAP_MS or delta < 0:
            delta = 0.0

        if self.running and delta > 0:
            self.net_ms += delta
            self.t2_el += delta
            self.t3_el += delta
            self.dirty = True
            fired = []
            if self.t2_el >= self.t2_ms:
                fired.append('T2')
                self.t2_el = 0.0
            if self.t3_el >= self.t3_ms:
                fired.append('T3')
                self.t3_el = 0.0
                # T3 窗口包含 T2，一起复位，否则会立刻重复弹一次
                self.t2_el = 0.0
            if fired:
                self.on_due(fired)
            self.render()

        if self.dirty and time.perf_counter() - self._last_save > 1.0:
            self._last_save = time.perf_counter()
            self.save()

        self.root.after(TICK_MS, self.tick)

    def on_due(self, fired):
        names = ' 和 '.join(fired)
        mins = (self.t2_ms // 60000) if 'T2' in fired else (self.t3_ms // 60000)
        self.banner_text = '%s 回顾节点到了（每 %d 分钟）· %s' % (
            names, mins, '计时已暂停，确认后点继续' if self.pause_on_due else '已自动开始下一轮')
        if self.sound:
            beep(self.root)
        if self.pause_on_due:
            self.running = False
            self._last_save = 0.0
            self.save()
        else:
            self.root.after(6000, self._clear_banner)

    def _clear_banner(self):
        if not self.running:
            self.banner_text = ''
            self.render()

    # ---------- 操作 ----------
    def toggle(self):
        self.running = not self.running
        if self.running:
            self.banner_text = ''
            self.last_tick = time.perf_counter()
        self.save()
        self.render()

    def reset(self):
        self.net_ms = 0.0
        self.t2_el = 0.0
        self.t3_el = 0.0
        self.banner_text = ''
        self.last_tick = time.perf_counter()
        self.save()
        self.render()

    def _on_period(self):
        for sp, attr in ((self.sp_t2, 't2_ms'), (self.sp_t3, 't3_ms')):
            try:
                v = int(sp.get())
            except Exception:
                v = 0
            v = max(1, min(600, v))
            setattr(self, attr, v * 60 * 1000)
        self.ring_t2['label'].configure(text='T2 · 每 %d 分钟' % (self.t2_ms // 60000))
        self.ring_t3['label'].configure(text='T3 · 每 %d 分钟' % (self.t3_ms // 60000))
        self.save()
        self.render()

    def _on_flag(self):
        self.sound = bool(self.sound_var.get())
        self.pause_on_due = bool(self.pause_var.get())
        self.topmost = bool(self.top_var.get())
        self._apply_topmost()
        self.save()

    def _on_topmost(self):
        self.topmost = bool(self.top_var.get())
        self._apply_topmost()
        self.save()

    def _apply_topmost(self):
        try:
            self.root.attributes('-topmost', self.topmost)
        except Exception:
            pass

    def _sync_controls(self):
        for sp, v in ((self.sp_t2, self.t2_ms // 60000), (self.sp_t3, self.t3_ms // 60000)):
            sp.delete(0, 'end')
            sp.insert(0, str(v))
        self.sound_var.set(self.sound)
        self.pause_var.set(self.pause_on_due)
        self.top_var.set(self.topmost)

    # ---------- 存档 ----------
    def snapshot(self):
        return {
            'version': 1,
            'running': self.running,
            'netMs': round(self.net_ms, 1),
            't2Ms': self.t2_ms // 60000,
            't3Ms': self.t3_ms // 60000,
            't2Elapsed': round(self.t2_el, 1),
            't3Elapsed': round(self.t3_el, 1),
            'pauseOnDue': self.pause_on_due,
            'sound': self.sound,
            'topmost': self.topmost,
            'savedAt': int(time.time() * 1000),
        }

    def save(self):
        self.dirty = False
        save_state(self.snapshot())

    def on_close(self):
        self.save()
        self.root.destroy()


def main():
    root = tk.Tk()
    TimerApp(root)
    root.mainloop()


if __name__ == '__main__':
    main()