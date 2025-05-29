import os
import subprocess
from dotenv import load_dotenv
import re
import logging
from notify.telegram import TelegramNotifier
from notify.email import EmailNotifier

# 初始化logger
logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)

# 配置日志
import os

# 获取当前文件所在目录
current_dir = os.path.dirname(os.path.abspath(__file__))
log_dir = os.path.join(current_dir, '../log')

# 确保日志目录存在
os.makedirs(log_dir, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(os.path.join(log_dir, 'server_ping_test.log')),
        logging.StreamHandler()
    ]
)

# 用于记录连续通知的次数和上次运行状态
NOTIFY_COUNT_FILE = os.path.join(log_dir, 'notify_count.txt')
LAST_RUN_STATUS_FILE = os.path.join(log_dir, 'last_run_status.txt')

def get_notify_count(file_path):
    try:
        with open(file_path, 'r') as f:
            return int(f.read().strip())
    except:
        return 0

def get_last_run_status():
    try:
        with open(LAST_RUN_STATUS_FILE, 'r') as f:
            return f.read().strip() == 'True'
    except:
        return True  # 如果文件不存在，假设上次是可访问的

def save_current_status(is_reachable):
    with open(LAST_RUN_STATUS_FILE, 'w') as f:
        f.write(str(is_reachable))

# 加载.env文件
load_dotenv()

OPENWRT_IP = os.getenv('OPENWRT_IP')
NAS_IP = os.getenv('NAS_IP')
NGINX_CONFIG_PATH_AVAILABLE = os.getenv('NGINX_CONFIG_PATH_AVAILABLE')

CANDIDATE_IP_LIST = [ip for ip in [NAS_IP, OPENWRT_IP] if ip]

# 记录上次状态
last_status = {ip: False for ip in CANDIDATE_IP_LIST}

def ping_host(host):
    try:
        # -c 1 表示只ping一次，-W 2 表示超时时间2秒
        result = subprocess.run([
            'ping', '-c', '3', '-W', '5', host  # 改为尝试3次，超时5秒
        ], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)

        current_status = result.returncode == 0
        
        # 检查状态变化
        if host in last_status and last_status[host] == False and current_status == True:
            # 从不可用恢复
            name_map = {NAS_IP: 'NAS', OPENWRT_IP: 'OPENWRT'}
            name = name_map.get(host, host)
            logger.info(f"🔄 {name}({host}) 服务已恢复，现在可用")
            
        last_status[host] = current_status
        return current_status
    except Exception as e:
        logger.error(f"执行ping命令时发生异常: {str(e)}", exc_info=True)
        return False

def get_first_reachable_ip_with_priority(nas_ip, openwrt_ip):
    # 如果NAS的IP可用，就优先使用NAS
    if nas_ip and ping_host(nas_ip):
        return nas_ip
    if openwrt_ip and ping_host(openwrt_ip):
        return openwrt_ip
    # 如果两个都不可用，返回None
    return None

def check_and_replace_nginx_proxy_ips_in_dir(conf_dir, candidate_ips):
    if not conf_dir or not os.path.isdir(conf_dir):
        logging.error(f"❌ Nginx配置目录不存在: {conf_dir}")
        return
    
    logging.info(f"🔍 检查Nginx配置目录: {conf_dir}")
    reload_needed = False
    switch_to_nas = False
    switch_to_openwrt = False
    config_files_count = 0
    
    for filename in os.listdir(conf_dir):
        if not filename.endswith('.conf'):
            continue
        config_files_count += 1
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
            # 如果当前IP不可访问，或者当前是OPENWRT但NAS可用，则进行切换
            if not ping_host(ip) or (ip == OPENWRT_IP and NAS_IP and ping_host(NAS_IP)):
                new_ip = get_first_reachable_ip_with_priority(NAS_IP, OPENWRT_IP)
                if new_ip and new_ip != ip:
                    name_map = {NAS_IP: 'NAS', OPENWRT_IP: 'OPENWRT'}
                    old_name = name_map.get(ip, ip)
                    new_name = name_map.get(new_ip, new_ip)
                    logging.info(f"🔄 {filename}: 代理从 {old_name}({ip}) 切换到 {new_name}({new_ip})")
                    new_conf = new_conf.replace(f"{prefix}{ip}{port}", f"{prefix}{new_ip}{port}")
                    changed = True
                    if new_ip == NAS_IP:
                        switch_to_nas = True
                    elif new_ip == OPENWRT_IP:
                        switch_to_openwrt = True
        if changed:
            with open(file_path, 'w') as f:
                f.write(new_conf)
            reload_needed = True
    
    if config_files_count == 0:
        logging.warning("⚠️ 未找到任何.conf配置文件")
    else:
        logging.info(f"✅ 检查完成，共处理 {config_files_count} 个配置文件")
    
    if reload_needed:
        logging.info("🔄 重新加载Nginx配置...")
        try:
            result = subprocess.run(['nginx', '-s', 'reload'], capture_output=True, text=True)
            if result.returncode != 0:
                logging.error(f"❌ Nginx重载失败: {result.stderr}")
            else:
                logging.info("✅ Nginx配置重载成功")
        except Exception as e:
            logging.error(f"❌ 执行Nginx重载失败: {e}")
    else:
        logging.info("ℹ️ 无需重载Nginx配置")
    # 切换通知
    if switch_to_nas or switch_to_openwrt:
        try:
            notify_types = [t.strip().lower() for t in os.getenv('NOTIFY_TYPE', 'telegram').split(',')]
            msg = "🚦 Nginx代理切换通知\n已切换到 " + ("🖥️ NAS" if switch_to_nas else "📶 OPENWRT")
            logging.info(f"📤 发送代理切换通知: {msg.split(chr(10))[1]}")
            for notify_type in notify_types:
                if notify_type == 'email':
                    notifier = EmailNotifier()
                    notifier.send_message("🚦 Nginx代理切换通知", msg, content_type="plain")
                elif notify_type == 'telegram':
                    notifier = TelegramNotifier()
                    notifier.send_message(msg)
        except Exception as e:
            logging.error(f"❌ 发送切换通知失败: {e}")

def print_ip_reachability(ip_list):
    name_map = {}
    if NAS_IP:
        name_map[NAS_IP] = 'NAS'
    if OPENWRT_IP:
        name_map[OPENWRT_IP] = 'OPENWRT'
    all_unreachable = True
    for ip in ip_list:
        name = name_map.get(ip, ip)
        if ping_host(ip):
            logging.info(f"✅ {name}（{ip}）可达")
            all_unreachable = False
        else:
            logging.warning(f"❌ {name}（{ip}）不可达")
    return all_unreachable

def main():
    logging.info("=" * 50)
    logging.info("🚀 开始服务器可达性检测")
    logging.info("=" * 50)
    
    all_unreachable = print_ip_reachability(CANDIDATE_IP_LIST)
    last_run_unreachable = not get_last_run_status()
    
    # 保存当前状态供下次运行使用
    save_current_status(not all_unreachable)
    
    # 如果上次全部不可达，这次有可达的，发送恢复通知
    if last_run_unreachable and not all_unreachable:
        try:
            # 获取可访问的设备名称列表
            available_devices = []
            name_map = {NAS_IP: 'NAS', OPENWRT_IP: 'OPENWRT'}
            for ip in CANDIDATE_IP_LIST:
                if ping_host(ip):
                    name = name_map.get(ip, ip)
                    available_devices.append(name)
            
            notify_types = [t.strip().lower() for t in os.getenv('NOTIFY_TYPE', 'telegram').split(',')]
            msg = f"🔄 服务恢复通知\n以下设备现在可以访问：\n" + "\n".join([f"✅ {device}" for device in available_devices])
            for notify_type in notify_types:
                if notify_type == 'email':
                    notifier = EmailNotifier()
                    notifier.send_message("🔄 服务恢复通知", msg, content_type="plain")
                    logging.info("✅ Email恢复通知发送成功")
                elif notify_type == 'telegram':
                    notifier = TelegramNotifier()
                    notifier.send_message(msg)
                    logging.info("✅ Telegram恢复通知发送成功")
        except Exception as e:
            logger.error(f"❌ 发送服务恢复通知失败: {e}")
    
    if all_unreachable:
        logging.warning("⚠️ 所有候选IP均不可达")
        notify_count = get_notify_count(NOTIFY_COUNT_FILE)
        
        # 如果连续通知次数已达到2次，只记录日志不发送通知
        if notify_count >= 2:
            logging.info(f"📊 已连续通知{notify_count}次，本次只记录日志不发送通知")
            with open(NOTIFY_COUNT_FILE, 'w') as f:
                f.write(str(notify_count + 1))
            return
            
        logging.info("📤 发送不可达警告通知...")
        try:
            notify_types = [t.strip().lower() for t in os.getenv('NOTIFY_TYPE', 'telegram').split(',')]
            for notify_type in notify_types:
                if notify_type == 'email':
                    notifier = EmailNotifier()
                    logging.info("📧 发送Email通知: 所有候选IP均不可达")
                    success = notifier.send_message("所有候选IP均不可达", "所有候选IP均不可达，请检查网络！", content_type="plain")
                    if success:
                        logging.info("✅ Email通知发送成功")
                    else:
                        logging.error("❌ Email通知发送失败")
                elif notify_type == 'telegram':
                    notifier = TelegramNotifier()
                    logging.info("📱 发送Telegram通知: 所有候选IP均不可达")
                    success, err = notifier.send_message("所有候选IP均不可达，请检查网络！")
                    if success:
                        logging.info("✅ Telegram通知发送成功")
                    else:
                        logging.error(f"❌ Telegram通知发送失败: {err}")
            # 更新通知计数
            with open(NOTIFY_COUNT_FILE, 'w') as f:
                f.write(str(notify_count + 1))
        except Exception as e:
            logging.error(f"❌ 调用通知失败: {e}")
        return
    else:
        # 如果IP可达，重置通知计数
        with open(NOTIFY_COUNT_FILE, 'w') as f:
            f.write('0')
        logging.info("📊 重置通知计数器")
    
    logging.info("🔧 检查 Nginx sites-available 目录下的配置...")
    check_and_replace_nginx_proxy_ips_in_dir(NGINX_CONFIG_PATH_AVAILABLE, CANDIDATE_IP_LIST)
    logging.info("✅ 服务器可达性检测完成")
    # TODO: 可扩展 Caddy 配置的处理

if __name__ == "__main__":
    main()
