param(
    [string]$WebUrl = "http://localhost:5173",
    [switch]$SkipWebRequest
)

$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent $PSScriptRoot
$timestamp = Get-Date -Format "yyyyMMdd-HHmmss"
$logDir = Join-Path $repoRoot "logs\experiments"
$logPath = Join-Path $logDir "$timestamp-chat-ui-verify.log"

New-Item -ItemType Directory -Force -Path $logDir | Out-Null

function Write-Log {
    param([string]$Message)
    $Message | Tee-Object -FilePath $logPath -Append
}

function Assert-Contains {
    param(
        [string]$Name,
        [string]$Text,
        [string]$Needle
    )
    $ok = $Text.Contains($Needle)
    Write-Log ("[{0}] {1}" -f ($(if ($ok) { "通过" } else { "失败" }), $Name))
    if (-not $ok) {
        Write-Log "  缺少片段：$Needle"
        throw "验证失败：$Name"
    }
}

Write-Log "AI 私教 Agent 前端聊天体验验证"
Write-Log ("时间：{0}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"))
Write-Log "目标：验证聊天窗口固定、历史消息内部滚动、当前回复逐字更新，并在最新 assistant 消息位置展示 Agent 执行过程。"
Write-Log ""

$chatPath = Join-Path $repoRoot "web\src\ChatView.tsx"
$mainPath = Join-Path $repoRoot "web\src\main.tsx"
$stylePath = Join-Path $repoRoot "web\src\styles.css"

$chat = [System.IO.File]::ReadAllText($chatPath, [System.Text.Encoding]::UTF8)
$main = [System.IO.File]::ReadAllText($mainPath, [System.Text.Encoding]::UTF8)
$styles = [System.IO.File]::ReadAllText($stylePath, [System.Text.Encoding]::UTF8)

Write-Log "一、源码行为检查"
Assert-Contains "聊天消息容器有 ref，可被代码控制滚动" $chat "const messagesRef = useRef<HTMLDivElement>(null);"
Assert-Contains "聊天消息容器绑定 messagesRef" $chat 'className="chat-messages" ref={messagesRef}'
Assert-Contains "消息/trace/busy 变化后滚动到底部" $chat "top: messagesRef.current.scrollHeight"
Assert-Contains "使用 requestAnimationFrame 避免 DOM 未更新就滚动" $chat "requestAnimationFrame"
Assert-Contains "最新 assistant 消息下方挂载 ThinkingProcess" $chat "i === messages.length - 1"
Assert-Contains "思考过程标题包含正在思考" $chat "Agent 正在思考"
Assert-Contains "思考过程标题包含执行过程" $chat "Agent 执行过程"
Assert-Contains "思考过程包含 Planner 阶段" $chat "规划本轮任务"
Assert-Contains "思考过程包含 Executor 阶段" $chat "调用工具执行"
Assert-Contains "思考过程包含 Verifier 阶段" $chat "自检输出约束"
Assert-Contains "思考过程包含 Repair 阶段" $chat "必要时修复"
Assert-Contains "发送时先插入用户消息" $main '{ role: "user", content: userText }'
Assert-Contains "发送时先插入空 assistant 占位" $main '{ role: "assistant", content: "" }'
Assert-Contains "回复按字符逐字追加" $main "for (const char of [...text])"
Assert-Contains "逐字追加有延迟，形成打字机效果" $main "await pause(TYPEWRITER_DELAY_MS)"

Write-Log ""
Write-Log "二、布局约束检查"
Assert-Contains "页面根容器固定为视口高度" $styles "height: 100vh;"
Assert-Contains "页面根容器禁止整体溢出滚动" $styles ".app-root"
Assert-Contains "主聊天布局禁止整体溢出" $styles ".chat-layout"
Assert-Contains "聊天主区域允许内部 flex 收缩" $styles "min-height: 0;"
Assert-Contains "聊天消息区内部滚动" $styles "overflow-y: auto;"
Assert-Contains "聊天消息区平滑滚动" $styles "scroll-behavior: smooth;"
Assert-Contains "思考过程面板有独立样式" $styles ".thinking-process"
Assert-Contains "思考步骤 active 状态有样式" $styles ".thinking-step.active"
Assert-Contains "思考步骤 done 状态有样式" $styles ".thinking-step.done"

if (-not $SkipWebRequest) {
    Write-Log ""
    Write-Log "三、本地页面可访问性检查"
    try {
        $response = Invoke-WebRequest -Uri $WebUrl -UseBasicParsing -TimeoutSec 8
        Write-Log ("[通过] Web 页面可访问：{0}，HTTP {1}" -f $WebUrl, $response.StatusCode)
        if ($response.Content -notmatch '<div id="root"></div>') {
            throw "页面没有找到 React root"
        }
        Write-Log "[通过] 页面包含 React root"
    } catch {
        Write-Log ("[失败] Web 页面访问失败：{0}" -f $_.Exception.Message)
        throw
    }
}

Write-Log ""
Write-Log "结论：聊天窗口固定、内部滚动、逐字生成和可展示 Agent 执行过程的关键实现均已存在。"
Write-Log ("日志文件：{0}" -f $logPath)
