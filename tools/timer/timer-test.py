# -*- coding: utf-8 -*-
"""独立计时器的逻辑断言（只测时间轴/到点/存档规则，不需要显示器）

与 demo/app.js 保持一致的几条机制，逐条钉住：
  · 时间差累加，不是计数器
  · 单次 tick 间隔 > IDLE_GAP_MS 不计入（切后台）
  · T2/T3 到点各自归零；T3 到点同时复位 T2
  · 暂停不计时；重启后从存档恢复
  · 只读不写：不碰真实的 timer-state.json
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

PASS = []
FAIL = []


def ok(cond, label):
    (PASS if cond else FAIL).append(label)
    print(('  ok  ' if cond else '  FAIL') + ' | ' + label)


def eq(a, b, label):
    ok(a == b, label + '  (得到 %r)' % (a,))


# ---------- 1. fmt 格式 ----------
print('\n[1] fmt 毫秒格式化')
import timer

eq(timer.fmt(0), '00:00', '0 → 00:00')
eq(timer.fmt(1000), '00:01', '1 秒 → 00:01')
eq(timer.fmt(5 * 60 * 1000), '05:00', '5 分钟 → 05:00')
eq(timer.fmt(30 * 60 * 1000), '30:00', '30 分钟 → 30:00')
eq(timer.fmt(59 * 1000 + 900), '00:59', '不足 1 秒向下取整')
eq(timer.fmt(3599 * 1000), '59:59', '59 分 59秒')
eq(timer.fmt(3600 * 1000), '1:00:00', '满 1 小时切换格式')
eq(timer.fmt(-5000), '00:00', '负值夹到 0')


# ---------- 2. 核心累加器（复刻 tick 的判定逻辑） ----------
print('\n[2] 净学习时长累加 + 切后台不计入')

T2 = 5 * 60 * 1000
T3 = 30 * 60 * 1000
IDLE = timer.IDLE_GAP_MS


class Acc(object):
    """把 TimerApp.tick 里的数值部分抽出来单独跑，省得依赖 GUI。"""

    def __init__(self, net=0.0, t2=0.0, t3=0.0):
        self.net_ms, self.t2_el, self.t3_el = net, t2, t3
        self.fired = []

    def step(self, delta):
        if delta > IDLE or delta < 0:
            delta = 0.0
        self.net_ms += delta
        self.t2_el += delta
        self.t3_el += delta
        if self.t2_el >= T2:
            self.fired.append('T2')
            self.t2_el = 0.0
        if self.t3_el >= T3:
            self.fired.append('T3')
            self.t3_el = 0.0
            self.t2_el = 0.0   # T3 窗口包含 T2，一起复位
        return delta


a = Acc()
a.step(200)
a.step(200)
a.step(200)
eq(round(a.net_ms), 600, '三次 200ms tick → 累加 600ms')

a = Acc()
a.step(10 * 60 * 1000)          # 切后台 10 分钟
eq(a.net_ms, 0, '单次间隔 10 分钟 → 一秒都不计')
eq(a.fired, [], '切后台不触发到点')

a = Acc()
a.step(-1000)
eq(a.net_ms, 0, '负 delta（时钟异常）不计')

a = Acc()
a.step(IDLE)                    # 边界：正好等于阈值仍计入
eq(a.net_ms, float(IDLE), 'delta 正好等于阈值 %d → 计入' % IDLE)
a = Acc()
a.step(IDLE + 1)
eq(a.net_ms, 0, 'delta 超阈值 1ms → 不计')

# 漂移检验：200ms tick 跑 5 分钟，与真实 5 分钟的误差应 < 一个 tick
a = Acc()
n = 0
while a.net_ms < T2:
    a.step(200)
    n += 1
eq(n, 1500, '5 分钟 = 1500 次 200ms tick')
ok(abs(a.net_ms - T2) < 200, '1500 次 200ms tick 累计误差 %d ms < 一个 tick' % abs(a.net_ms - T2))


# ---------- 3. 到点触发 ----------
print('\n[3] T2 / T3 到点')


def run(acc, total_ms, step=200):
    """按真实节奏喂 tick。不能用一步喂完 —— 超过 IDLE_GAP_MS 会被判切后台丢掉。"""
    left = total_ms
    while left > 0:
        d = min(step, left)
        acc.step(d)
        left -= d
    return acc


a = run(Acc(), T2)
eq(a.fired, ['T2'], '满 5 分钟 → 触发 T2')
eq(a.t2_el, 0, 'T2 触发后归零')
eq(round(a.t3_el), T2, 'T2 触发不影响 T3 计时')

a = run(Acc(), T3)
eq(a.fired.count('T3'), 1, '满 30 分钟 → 触发 T3 一次')
eq(a.t2_el, 0, 'T3 触发时 T2 一起归零（不会立刻重复弹）')
eq(a.t3_el, 0, 'T3 归零')

# 30 分钟里 T2 应该响了 5 次：5,10,15,20,25 → 30 分那一刻的 T2 被 T3 一起吞掉
a = run(Acc(), T3 - 100)
eq(a.fired.count('T2'), 5, '30 分钟内 T2 共响 5 次（实得 %d）' % a.fired.count('T2'))
ok('T3' not in a.fired, '未到 30 分不触发 T3')

# 反证：一步喂 5 分钟会被切后台规则吃掉，不触发 —— 这正是规则在起作用
b = Acc()
b.step(T2)
eq(b.fired, [], '一步喂 5 分钟反被丢弃（切后台规则生效，不会误报到点）')


# ---------- 4. 存档 ----------
print('\n[4] 存档格式与往返')

snap = {
    'version': 1, 'running': True, 'netMs': 1234.0,
    't2Ms': 5, 't3Ms': 30, 't2Elapsed': 100.0, 't3Elapsed': 200.0,
    'pauseOnDue': True, 'sound': False, 'topmost': True,
}
tmp = os.path.join(os.path.dirname(os.path.abspath(__file__)), '_timer_probe.json')
_orig_state_path = timer.state_path     # 临时改路径，不碰真实文件
timer.state_path = lambda: tmp
timer.save_state(snap)
ok(os.path.exists(tmp), '存档文件写出')
back = json.load(open(tmp, encoding='utf-8'))
eq(back['netMs'], 1234.0, 'netMs 往返一致')
eq(back['t2Elapsed'], 100.0, 't2Elapsed 往返一致')
eq(back['pauseOnDue'], True, '布尔字段往返一致')

timer.save_state({'version': 1, 'netMs': 9})
eq(timer.load_state()['netMs'], 9, 'save_state 接受 dict 直接写')

os.remove(tmp)
ok(not os.path.exists(tmp), '探针文件已清理')
# 还原真实存档路径（后面还要断言它）
timer.state_path = _orig_state_path
ok(timer.state_path().endswith('timer-state.json'), 'state_path 指向 timer-state.json')


# ---------- 5. 常量与项目一致 ----------
print('\n[5] 常量与 demo/app.js 对齐')
eq(timer.DEFAULT_T2_MIN, 5, 'T2 默认 5 分钟（app.js T2_MS）')
eq(timer.DEFAULT_T3_MIN, 30, 'T3 默认 30 分钟（app.js T3_MS）')
eq(timer.IDLE_GAP_MS, 700, 'IDLE_GAP_MS = 700ms（app.js 同值）')
eq(timer.TICK_MS, 200, 'TICK_MS = 200ms（app.js 同值）')


print('\n================ %d 通过 / %d 失败 ================' % (len(PASS), len(FAIL)))
if FAIL:
    for f in FAIL:
        print('  FAILED: ' + f)
    sys.exit(1)