import os
import subprocess
from dotenv import load_dotenv
import re
from notify.telegram import TelegramNotifier

# 加载.env文件
load_dotenv()

OPENWRT_IP = os.getenv('OPENWRT_IP')
NAS_IP = os.getenv('NAS_IP')
NGINX_CONFIG_PATH_AVAILABLE = os.getenv('NGINX_CONFIG_PATH_AVAILABLE')

CANDIDATE_IP_LIST = [ip for ip in [NAS_IP, OPENWRT_IP] if ip]

def ping_host(host):
    try:
        # -c 1 表示只ping一次，-W 2 表示超时时间2秒
        result = subprocess.run([
            'ping', '-c', '1', '-W', '2', host
        ], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        return result.returncode == 0
    except Exception:
        return False

def get_first_reachable_ip(ip_list):
    for ip in ip_list:
        if ping_host(ip):
            return ip
    return None

def check_and_replace_nginx_proxy_ips_in_dir(conf_dir, candidate_ips):
    if not conf_dir or not os.path.isdir(conf_dir):
        print(f"❌ Nginx 配置目录未找到: {conf_dir}")
        return
    for filename in os.listdir(conf_dir):
        file_path = os.path.join(conf_dir, filename)
        if not os.path.isfile(file_path):
            continue
        with open(file_path, 'r') as f:
            conf = f.read()
        # 匹配 proxy_pass http://IP:PORT 或 proxy_pass https://IP:PORT
        pattern = r'(proxy_pass\s+https?://)([\d.]+)(:[\d]+)?'
        matches = list(re.finditer(pattern, conf))
        if not matches:
            continue
        new_conf = conf
        changed = False
        for m in matches:
            prefix, ip, port = m.group(1), m.group(2), m.group(3) or ''
            if not ping_host(ip):
                new_ip = get_first_reachable_ip(candidate_ips)
                if new_ip and new_ip != ip:
                    print(f"[{filename}] 替换 proxy_pass: {ip} -> {new_ip}")
                    new_conf = new_conf.replace(f"{prefix}{ip}{port}", f"{prefix}{new_ip}{port}")
                    changed = True
                else:
                    print(f"[{filename}] ❌ 没有可用的IP替换 {ip}")
        if changed:
            backup_path = file_path + '.bak'
            with open(backup_path, 'w') as f:
                f.write(conf)
            with open(file_path, 'w') as f:
                f.write(new_conf)
            print(f"[{filename}] ✅ 已更新并备份原配置到 {backup_path}")
        else:
            if matches:
                print(f"[{filename}] 所有 proxy_pass IP 均可达，无需更改。")

def print_ip_reachability(ip_list):
    all_unreachable = True
    for ip in ip_list:
        if ping_host(ip):
            print(f"✅ {ip} 可达")
            all_unreachable = False
        else:
            print(f"❌ {ip} 不可达")
    return all_unreachable

def main():
    print("检测候选IP可达性：")
    all_unreachable = print_ip_reachability(CANDIDATE_IP_LIST)
    if all_unreachable:
        print("所有候选IP均不可达，发送Telegram警告...")
        try:
            notifier = TelegramNotifier()
            success, err = notifier.send_message("所有候选IP均不可达，请检查网络！")
            if success:
                print("已通过Telegram发送警告！")
            else:
                print(f"Telegram发送失败: {err}")
        except Exception as e:
            print(f"调用Telegram通知失败: {e}")
        return
    print("\n检查 Nginx sites-available 目录下的配置...")
    check_and_replace_nginx_proxy_ips_in_dir(NGINX_CONFIG_PATH_AVAILABLE, CANDIDATE_IP_LIST)
    # TODO: 可扩展 Caddy 配置的处理

if __name__ == "__main__":
    main()
