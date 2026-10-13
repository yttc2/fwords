/* MVC 分层后的脚本加载顺序。
 *
 * **必须与 demo/index.html 里的 <script> 顺序一致**：顺序就是依赖顺序，
 * 每层在加载时通过别名抓取上游层（var x = FW.model.x），顺序错了会抓到 undefined。
 *
 * 加层 / 改层时，这里和 index.html 一起改。
 */
module.exports = [
  'fw-core.js',        // 常量、存储、状态容器、通用工具
  'fw-model.js',       // 词表解析与分组、词库注册表、持久化、回顾调度
  'fw-speech.js',      // 发音（在线真人音源 + 系统 TTS 兜底）
  'fw-view.js',        // 渲染与视图切换
  'fw-controller.js',  // 事件绑定、手势、流程推进、计时循环
  'app.js'             // 引导：组装并启动
];
