import base64
import html
import subprocess
from datetime import datetime
from pathlib import Path


def build_summary(result: dict) -> str:
    def top(rows):
        return "、".join(str(x.get("code", "")) + str(x.get("name", "")) for x in rows[:3]) or "無"
    status = result.get("optimization", {}).get("status", "學習狀態未知")
    warnings = []
    for row in result.get("long_top10", []) + result.get("short_top10", []):
        research = row.get("broker_risk_research") or {}
        if not research.get("warning"):
            continue
        branches = ([f"買超 {item.get('branch', '')}" for item in research.get("watched_buyers", [])] +
                    [f"賣超 {item.get('branch', '')}" for item in research.get("watched_sellers", [])])
        warnings.append(f"{row.get('code', '')}{row.get('name', '')}({','.join(filter(None, branches))})")
    warning_text = f"｜分點風險：{'、'.join(warnings)}" if warnings else ""
    excluded = result.get("execution_eligibility", {}).get("filtered_out", [])
    exclusion_text = (f"｜官方當沖資格排除：{top(excluded)}" if excluded else "")
    return (f"{result.get('data_date', '')}｜偏多：{top(result.get('long_top10', []))}｜"
            f"偏空：{top(result.get('short_top10', []))}{exclusion_text}{warning_text}｜{status}｜規則式研究觀察，非投資建議")


def notify(message: str, directory: Path):
    """優先送 Windows Toast；失敗時留下可見文字檔。"""
    directory.mkdir(parents=True, exist_ok=True)
    safe = html.escape(message)
    script = f'''$xml = New-Object Windows.Data.Xml.Dom.XmlDocument
$xml.LoadXml('<toast><visual><binding template="ToastGeneric"><text>台股當沖觀察名單</text><text>{safe}</text><text>研究觀察工具，非投資建議</text></binding></visual></toast>')
$toast = [Windows.UI.Notifications.ToastNotification]::new($xml)
[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier('TaiStockScanner').Show($toast)'''
    encoded = base64.b64encode(script.encode("utf-16le")).decode("ascii")
    try:
        subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded], check=True, timeout=15, capture_output=True)
        return None
    except (OSError, subprocess.SubprocessError):
        path = directory / "notification.txt"
        path.write_text(f"{datetime.now().isoformat(timespec='seconds')}\n{message}\n研究觀察工具，非投資建議\n", encoding="utf-8")
        return path
