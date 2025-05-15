import os
from datetime import datetime, timedelta

def create_ics_file_multi(events, output_path):
    """
    生成包含多个证书过期事件的ics文件
    :param events: [(domain, expire_date_str), ...]
    :param output_path: ics文件路径
    :return: ics文件路径
    """
    ics_content = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//NASBridge//SSL Expiry//EN"
    ]
    for domain, expire_date_str in events:
        expire_date = datetime.strptime(expire_date_str, '%Y-%m-%d %H:%M:%S GMT')
        start = expire_date - timedelta(hours=1)
        end = expire_date
        ics_content.extend([
            "BEGIN:VEVENT",
            f"UID:{domain}-{expire_date_str}@ssl-expiry",
            f"DTSTAMP:{datetime.utcnow().strftime('%Y%m%dT%H%M%SZ')}",
            f"DTSTART:{start.strftime('%Y%m%dT%H%M%SZ')}",
            f"DTEND:{end.strftime('%Y%m%dT%H%M%SZ')}",
            f"SUMMARY:SSL证书即将过期: {domain}",
            f"DESCRIPTION:{domain} 的SSL证书将于 {expire_date_str} 过期",
            "END:VEVENT"
        ])
    ics_content.append("END:VCALENDAR")
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        f.write("\n".join(ics_content))
    return output_path

def create_ics_file(domain, expire_date_str, output_dir="log"):
    """
    生成单个证书事件的ics文件（兼容旧用法）
    """
    output_path = os.path.join(output_dir, f"{domain}_ssl_expiry.ics")
    return create_ics_file_multi([(domain, expire_date_str)], output_path) 