# -*- coding: utf-8 -*-
"""GUI 层冒烟测试：实例化真窗口，模拟 tick，检查渲染结果

不进 mainloop，所以不会卡住；跑完自动销毁。
"""
import os
import sys
import time
import tkinter as tk

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import timer

FAIL = []
PASS = []


def ok(cond, label):
    (PASS if cond else FAIL).append(label)
    print(('  ok  ' if cond else '  FAIL') + ' | ' + label)


# 隔离存档，别碰真实文件
_tmp = os.path.join(os.path.dirname(os.path.abspath(__file__)), '_gui_probe.json')
_orig = timer.state_path
timer.state_path = lambda: _tmp
timer.save_state({'version': 1, 'running': False, 'netMs': 0, 't2Ms': 5,
                  't3Ms': 30, 'pauseOnDue': False, 'sound': False, 'topmost': True})

root = tk.Tk()
# 用 alpha=0 让窗口已映射但完全透明：不弹到前台、不打扰，也不会被环境当 GUI 进程杀掉。
# 注意不能先 withdraw() —— withdraw 掉的窗口 attributes('-alpha') 不会重新映射。
root.attributes('-alpha', 0.0)
app = timer.TimerApp(root)

print('\n[1] 初始化')
ok(app.net_ms == 0, '净学习时长从 0 起')
ok(app.t2_ms == 5 * 60 * 1000, 'T2 = 5 分钟')
ok(app.t3_ms == 30 * 60 * 1000, 'T3 = 30 分钟')
ok(app.running is False, '默认暂停')
ok(len(app._rings) == 2, '两个倒计时环已创建')

print('\n[2] 渲染')
app.render()
ok(app.big.cget('text') == '00:00', '大数字初始 00:00')
for r in app._rings:
    n_arc = len(r['canvas'].find_all())
    ok(n_arc >= 1, '环有图元（%d 个）' % n_arc)

app.net_ms = 125000
app.render()
ok(app.big.cget('text') == '02:05', '大数字 02:05（实得 %s）' % app.big.cget('text'))

# 中间文本应是剩余时间
items = app.ring_t2['canvas'].find_all()
texts = [app.ring_t2['canvas'].itemcget(i, 'text') for i in items
         if app.ring_t2['canvas'].type(i) == 'text']
ok('05:00' in texts, 'T2 环中间显示 05:00（实得 %r）' % texts)

print('\n[3] 开始 / 暂停')
app.toggle()
ok(app.running is True, '点开始 → 运行中')
ok(app.btn_run.cget('text') == '暂停', '按钮变「暂停」')
ok('运行中' in app.dot.cget('text'), '状态灯显示运行中')
app.toggle()
ok(app.running is False, '再点 → 暂停')
ok(app.big.cget('fg') == timer.MUTED, '暂停时大数字变灰')

print('\n[4] 到点提示条')
# 提示条显隐必须用「已映射的窗口」验：root 处于 withdraw 状态时
# pack/pack_forget 不会真正生效，测出来是假象。
root.update()
ok(root.winfo_ismapped() == 1, '窗口已映射（透明，不打扰）')
app.banner_text = 'T2 回顾节点到了'
app.render()
root.update()
ok(app.banner.winfo_ismapped() == 1, '提示条已显示在窗口上')
ok('T2' in app.banner.cget('text'), '提示条文案正确')
app.banner_text = ''
app.render()
root.update()
ok(app.banner.winfo_ismapped() == 0, '清空后提示条隐藏')

print('\n[5] 到点处理（不发声，模拟 pause_on_due）')
app.sound = False
app.pause_on_due = True
app.running = True
app.t2_el = app.t2_ms - 500
# 注意：单次 delta 必须 < IDLE_GAP_MS(700)，否则会被判切后台丢弃 —— 所以这里喂 500ms
app.last_tick = time.perf_counter() - 0.5
app.tick()
ok(app.running is False, '勾了「到点后暂停」→ 计时已停')
ok('回顾节点' in app.banner_text, '到点弹出提示（实得 %r）' % app.banner_text)
ok(app.t2_el < app.t2_ms, 'T2 计数已归零重开下一轮')
root.update()
ok(app.banner.winfo_ismapped() == 1, '到点后提示条确实显示在窗口上')

print('\n[6] 改周期')
app.pause_on_due = False
app.sp_t2.delete(0, 'end')
app.sp_t2.insert(0, '3')
app._on_period()
ok(app.t2_ms == 3 * 60 * 1000, 'T2 改成 3 分钟')
ok('3' in app.ring_t2['label'].cget('text'), 'T2 标签跟着更新（%s）'
   % app.ring_t2['label'].cget('text'))
app.sp_t2.delete(0, 'end')
app.sp_t2.insert(0, 'abc')          # 非法输入
app._on_period()
ok(app.t2_ms >= 60 * 1000, '非法输入被夹到 ≥1 分钟，没崩')
app.sp_t2.delete(0, 'end')
app.sp_t2.insert(0, '5')
app._on_period()

print('\n[7] 存档往返（真窗口 → 存 → 重开）')
app.net_ms = 654321
app.running = True
app.save()
app2 = timer.TimerApp(root)
ok(abs(app2.net_ms - 654321) < 1, '重开后净时长恢复（实得 %d）' % app2.net_ms)
ok(app2.running is True, '重开后运行状态恢复')
app2.reset()
ok(app2.net_ms == 0, '重置归零')
ok(app2.t2_el == 0 and app2.t3_el == 0, '重置把两个环也归零')

root.destroy()
timer.state_path = _orig
if os.path.exists(_tmp):
    os.remove(_tmp)
print('\n[清理] 探针文件已删除: %s' % (not os.path.exists(_tmp)))

print('\n================ %d 通过 / %d 失败 ================' % (len(PASS), len(FAIL)))
if FAIL:
    for f in FAIL:
        print('  FAILED: ' + f)
    sys.exit(1)