import yaml
import subprocess
from datetime import datetime
from rich.console import Console
from rich.table import Table
from rich import box
import os
import sys
import re
sys.path.append(os.path.join(os.path.dirname(__file__), "notify"))
from telegram import TelegramNotifier

CONFIG_PATH = os.path.join(os.path.dirname(__file__), "../domains_config.yaml")
CONFIG_PATH = os.path.abspath(CONFIG_PATH)
PING_COUNT = 4
TIMEOUT_SEC = 2

console = Console()

def load_targets(config_path):
    with open(config_path, "r") as f:
        data = yaml.safe_load(f)
    return data.get("zt_ip", [])

def ping(ip):
    try:
        # -c: count, -W: timeout (Linux), -t: timeout (macOS)
        result = subprocess.run(
            ["ping", "-c", str(PING_COUNT), "-W", str(TIMEOUT_SEC), ip],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        return result.returncode == 0
    except Exception:
        return False

def send_telegram_report(report):
    try:
        notifier = TelegramNotifier()  # 需配置好环境变量
        ok, err = notifier.send_message(report)
        if ok:
            console.print("[green]Telegram 报告已发送[/green]")
        else:
            console.print(f"[red]Telegram 发送失败: {err}[/red]")
    except Exception as e:
        console.print(f"[red]Telegram 发送异常: {e}[/red]")

def get_type_icon(t):
    ip = str(t['ip'])
    name = t['name'].lower()
    # 内网IP判断
    if (ip.startswith("10.") or
        ip.startswith("192.168.") or
        re.match(r"172\\.(1[6-9]|2[0-9]|3[0-1])\\.", ip) or
        name in ["nas", "router", "host", "macbook"]):
        return "🏠", "lan"
    # Google
    if "google" in name or "google" in ip:
        return "🌐", "google"
    # GPT/OpenAI
    if "gpt" in name or "openai" in name or "openai" in ip:
        return "🤖", "gpt"
    # YouTube
    if "youtube" in name or "youtube" in ip:
        return "📺", "youtube"
    # 其它外网
    return "🌍", "wan"

def main():
    targets = load_targets(CONFIG_PATH)
    if not targets:
        console.print("[red]未在配置文件中找到任何目标！[/red]")
        return

    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    title_line = f"[bold magenta]{'='*10} NAS连通性测试 {'='*10}[/bold magenta]"
    console.print(title_line)
    console.print(f"[bold]测试时间:[/bold] [cyan]{now}[/cyan]")
    console.print(f"[bold]测试目标 ({len(targets)}):[/bold]")
    for t in targets:
        console.print(f"  [yellow]{t['name']}[/yellow]: [white]{t['ip']}[/white]")
    console.print("[bold magenta]" + "-"*40 + "[/bold magenta]")

    table = Table(show_header=True, header_style="bold magenta", box=box.SIMPLE_HEAVY)
    table.add_column("名称", style="cyan", justify="center")
    table.add_column("IP", style="white", justify="center")
    table.add_column("状态", style="white", justify="center")

    failed = []
    for t in targets:
        ok = ping(str(t["ip"]))
        if ok:
            table.add_row(f"[yellow]{t['name']}[/yellow]", f"[white]{t['ip']}[/white]", "[green]✅[/green]")
        else:
            table.add_row(f"[yellow]{t['name']}[/yellow]", f"[white]{t['ip']}[/white]", "[red]❌[/red]")
            failed.append(t)

    console.print(table)
    console.print("[bold magenta]" + "-"*40 + "[/bold magenta]")
    passed_count = len(targets) - len(failed)

    # 分类
    lan_targets = []
    wan_targets = []
    icon_map = {}
    for t in targets:
        icon, ttype = get_type_icon(t)
        icon_map[t['ip']] = icon
        if ttype == "lan":
            lan_targets.append(t)
        else:
            wan_targets.append(t)

    # 构建表格头和内容（去掉类型列）
    table_lines = []
    table_lines.append(f"{'名称':<10}{'IP':<18}{'状态':<6}")
    for t in targets:
        status = "✅ 正常" if t not in failed else "❌ 失败"
        icon = icon_map[t['ip']]
        table_lines.append(f"{t['name']:<10}{t['ip']:<18}{status:<6}")

    # 总结语
    lan_failed = [t for t in lan_targets if t in failed]
    wan_failed = [t for t in wan_targets if t in failed]
    if not lan_failed:
        lan_summary = "🏠 内网服务全部正常"
    else:
        lan_summary = "🏠 内网有服务无法访问"
    if not wan_failed:
        wan_summary = "🌍 国外服务全部可达"
    else:
        wan_summary = "🌍 国外服务有无法访问"

    # 构建报告
    report_lines = []
    report_lines.append("<b>========== NAS连通性测试 ==========</b>")
    report_lines.append(f"测试时间: <code>{now}</code>")
    report_lines.append(f"测试目标 ({len(targets)}):\n")
    report_lines.append("<pre>" + "\n".join(table_lines) + "</pre>")
    report_lines.append(f"通过: {passed_count}/{len(targets)}  失败: {len(failed)}/{len(targets)}\n")
    report_lines.append(lan_summary)
    report_lines.append(wan_summary)
    if failed:
        report_lines.append("❌ 以下目标连接失败：")
        for t in failed:
            icon = icon_map[t['ip']]
            report_lines.append(f"  - {icon} {t['name']}: {t['ip']}")
    else:
        report_lines.append("✅ 所有目标连接测试通过")
    report = "\n".join(report_lines)

    # 输出到终端
    console.print(report.replace('<b>', '').replace('</b>', ''))

    # 有失败时发送Telegram
    if failed:
        send_telegram_report(report)

if __name__ == "__main__":
    main()