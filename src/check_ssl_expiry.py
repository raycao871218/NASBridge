import ssl
import socket
import json
import os
from datetime import datetime, timezone
from typing import Tuple, Optional, List
from notify.telegram import TelegramNotifier
from notify.email import EmailNotifier
from notify.ics_util import create_ics_file_multi
import yaml  # 新增：用于解析YAML配置文件
from cryptography import x509
from cryptography.hazmat.backends import default_backend

def parse_host_port(url: str) -> Tuple[str, int]:
    """解析URL，分离主机名和端口号

    Args:
        url: URL字符串，可能包含端口号

    Returns:
        Tuple[str, int]: 主机名和端口号
    """
    if ':' in url:
        hostname, port = url.rsplit(':', 1)
        return hostname, int(port)
    return url, 443

def check_ssl_expiry(url: str) -> Tuple[bool, Optional[str], Optional[str]]:
    """
    检查给定URL的SSL证书是否过期
    
    Args:
        url: URL地址，可以包含端口号（例如：example.com:8443）
        
    Returns:
        Tuple[bool, Optional[str], Optional[str]]:
            - bool: 证书是否有效（True为有效，False为无效或出错）
            - str: 过期时间（如果成功获取）
            - str: 错误信息（如果有错误）
    """
    try:
        hostname, port = parse_host_port(url)
        context = ssl.create_default_context()
        with socket.create_connection((hostname, port)) as sock:
            with context.wrap_socket(sock, server_hostname=hostname) as ssock:
                cert = ssock.getpeercert()
                
                # 获取证书过期时间
                expire_date = datetime.strptime(cert['notAfter'], '%b %d %H:%M:%S %Y GMT').replace(tzinfo=timezone.utc)
                current_date = datetime.now(timezone.utc)
                
                # 检查是否过期
                is_valid = expire_date > current_date
                
                return is_valid, expire_date.strftime('%Y-%m-%d %H:%M:%S GMT'), None
                
    except ssl.SSLCertVerificationError as e:
        # 如果是证书链验证失败，尝试获取证书信息但不验证证书链
        if "unable to get local issuer certificate" in str(e):
            try:
                hostname, port = parse_host_port(url)
                context = ssl.create_default_context()
                # 暂时禁用证书验证来获取证书信息
                context.check_hostname = False
                context.verify_mode = ssl.CERT_NONE
                
                with socket.create_connection((hostname, port)) as sock:
                    with context.wrap_socket(sock, server_hostname=hostname) as ssock:
                        # 获取二进制证书数据
                        cert_der = ssock.getpeercert(binary_form=True)
                        
                        if cert_der:
                            # 使用cryptography库解析证书
                            cert = x509.load_der_x509_certificate(cert_der, default_backend())
                            
                            # 获取证书过期时间
                            try:
                                # 使用新的UTC方法，如果不可用则使用旧方法
                                expire_date = cert.not_valid_after_utc
                            except AttributeError:
                                expire_date = cert.not_valid_after.replace(tzinfo=timezone.utc)
                            current_date = datetime.now(timezone.utc)
                            
                            # 检查是否过期
                            is_valid = expire_date > current_date
                            
                            # 返回证书信息，但标记为证书链验证失败
                            return is_valid, expire_date.strftime('%Y-%m-%d %H:%M:%S GMT'), f"证书链验证失败（证书本身有效）: {str(e)}"
                        else:
                            return False, None, f"证书验证失败且无法获取证书数据: {str(e)}"
                        
            except Exception as inner_e:
                return False, None, f"证书验证失败且无法获取证书信息: {str(inner_e)}"
        else:
            return False, None, f"证书验证失败: {str(e)}"
    except socket.gaierror as e:
        return False, None, f"DNS解析错误: {str(e)}"
    except Exception as e:
        return False, None, f"检查证书时发生错误: {str(e)}"

# 使用示例
def format_time_remaining(expire_date_str: str) -> str:
    """格式化剩余时间"""
    expire_date = datetime.strptime(expire_date_str, '%Y-%m-%d %H:%M:%S GMT').replace(tzinfo=timezone.utc)
    now = datetime.now(timezone.utc)
    remaining = expire_date - now
    
    days = remaining.days
    if days > 30:
        return f"{days // 30}个月"
    elif days > 0:
        return f"{days}天"
    else:
        hours = remaining.seconds // 3600
        return f"{hours}小时"

def load_domains(config_file: str = 'domains_config.yaml') -> List[str]:
    """从YAML配置文件加载域名列表

    Args:
        config_file: 配置文件路径，默认为domains_config.yaml

    Returns:
        list[str]: 域名列表
    """
    try:
        with open(config_file, 'r', encoding='utf-8') as f:
            data = yaml.safe_load(f)
            domains = data.get('domains', [])
            return domains
    except FileNotFoundError:
        print(f"错误: 找不到配置文件 {config_file}")
        return []
    except Exception as e:
        print(f"错误: 读取配置文件时发生错误: {str(e)}")
        return []

def save_check_result(url: str, is_valid: bool, expire_date: Optional[str], error: Optional[str], log_dir: str = 'log') -> None:
    """保存检查结果到日志文件

    Args:
        url: 检查的URL
        is_valid: 证书是否有效
        expire_date: 过期时间
        error: 错误信息
        log_dir: 日志目录
    """
    # 确保日志目录存在
    os.makedirs(log_dir, exist_ok=True)
    log_path = os.path.join(log_dir, 'ssl_check.log')
    
    # 获取当前时间
    check_time = datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S %Z')
    
    # 生成状态信息
    if error:
        status = f"错误: {error}"
    else:
        remaining_time = format_time_remaining(expire_date) if expire_date else "未知"
        status = f"{'有效' if is_valid else '已过期'}, 过期时间: {expire_date or '未知'}（还剩 {remaining_time}）"
    
    # 读取现有日志内容
    domains_status = {}
    if os.path.exists(log_path):
        with open(log_path, 'r', encoding='utf-8') as f:
            try:
                domains_status = json.loads(f.read())
            except json.JSONDecodeError:
                pass
    
    # 更新当前域名的状态
    domains_status[url] = {
        'check_time': check_time,
        'status': status
    }
    
    # 写入更新后的日志内容
    with open(log_path, 'w', encoding='utf-8') as f:
        json.dump(domains_status, f, ensure_ascii=False, indent=2)

if __name__ == '__main__':
    import os
    
    # 获取脚本所在目录的路径
    script_dir = os.path.dirname(os.path.abspath(__file__))
    # 获取项目根目录的路径（脚本所在目录的上一级）
    project_root = os.path.dirname(script_dir)
    # 构建配置文件的完整路径
    config_path = os.path.join(project_root, 'domains_config.yaml')
    
    domains = load_domains(config_path)
    if not domains:
        print("未找到要检查的域名，请检查配置文件。")
        exit(1)
    
    print("检查SSL证书状态...\n")
    
    # 初始化通知器
    telegram_notifier = TelegramNotifier()
    email_notifier = EmailNotifier()
    warning_messages = []
    warning_events = []  # 新增：用于收集即将过期的证书(domain, expire_date_str)
    
    for url in domains:
        print(f"正在检查: {url}...")

        is_valid, expire_date, error = check_ssl_expiry(url)
        
        # 保存检查结果到日志
        log_dir = os.path.join(project_root, 'log')
        save_check_result(url, is_valid, expire_date, error, log_dir)
        
        # 收集需要发送警告的消息
        if error:
            if "证书链验证失败（证书本身有效）" in error:
                # 证书链验证失败但证书本身有效，检查过期时间
                if expire_date:
                    expire_time = datetime.strptime(expire_date, '%Y-%m-%d %H:%M:%S GMT').replace(tzinfo=timezone.utc)
                    days_remaining = (expire_time - datetime.now(timezone.utc)).days
                    
                    remaining_time = format_time_remaining(expire_date)
                    icon = "⚠️"
                    status = "证书链验证失败（证书本身有效）"
                    
                    # 如果有HTTPS则跳过，没有增加HTTPS
                    if "https" in url:
                        url_format = url
                    else:
                        url_format = f"https://{url}"
                    
                    # 格式化消息
                    message = f"{icon} {url_format} \n    状态：{status}\n    过期时间：{expire_date}\n    剩余时间：{remaining_time} \n    错误：{error}"
                    print(message)
                    
                    # 如果剩余天数小于10天，也加入警告
                    if days_remaining < 10:
                        warning_messages.append(message)
                        warning_events.append((url, expire_date))
                    else:
                        print(f"注意：{url} 证书链验证失败但证书本身有效，还有 {remaining_time} 过期")
                else:
                    message = f"⚠️ {url}: 证书链验证失败 - {error}"
                    print(message)
                    warning_messages.append(message)
            elif "证书验证失败" in error:
                message = f"❌ {url}: 证书无效 - {error}"
                print(message)
                warning_messages.append(message)
            elif "DNS解析错误" in error:
                message = f"❌ {url}: 无法连接 - 域名解析失败"
                print(message)
                warning_messages.append(message)
            else:
                message = f"❌ {url}: 检查失败 - {error}"
                print(message)
                warning_messages.append(message)
        elif expire_date:
            # 解析过期时间
            expire_time = datetime.strptime(expire_date, '%Y-%m-%d %H:%M:%S GMT').replace(tzinfo=timezone.utc)
            days_remaining = (expire_time - datetime.now(timezone.utc)).days
            
            # 如果剩余天数小于10天，输出警告
            if days_remaining < 10:
                remaining_time = format_time_remaining(expire_date)
                icon = "⚠️"
                status = "即将过期" if is_valid else "已过期"
                # 如果有HTTPS则跳过，没有增加HTTPS
                if "https" in url:
                    url_format = url
                else:
                    url_format = f"https://{url}"
                # 格式化消息
                message = f"{icon} {url_format} \n    状态：{status}\n    过期时间：{expire_date}\n    剩余时间：{remaining_time} \n"
                print(message)
                warning_messages.append(message)
                warning_events.append((url, expire_date))  # 新增：收集即将过期的证书

    # 发送通知
    if warning_messages:
        message = "🔒 SSL证书状态警告\n\n" + "\n\n".join(warning_messages)
        # 生成ics附件（如果有即将过期的证书）
        ics_path = None
        if warning_events:
            today_str = datetime.now().strftime('%Y%m%d')
            ics_path = os.path.join(project_root, 'log', f'ssl_expiry_events_{today_str}.ics')
            create_ics_file_multi(warning_events, ics_path)
        # 发送通知
        success, error = telegram_notifier.send_message(message)
        email_success = email_notifier.send_message("SSL证书状态警告", message, attachments=[ics_path] if ics_path else None)
        if not success:
            print(f"\n发送Telegram通知失败：{error}")
        if not email_success:
            print(f"\n发送邮件通知失败")
    else:
        message = "✅ 所有SSL证书检查通过，状态正常"
        # 发送Telegram通知
        success, error = telegram_notifier.send_message(message)
        if not success:
            print(f"\n发送Telegram通知失败：{error}")
        # 发送邮件通知
        email_notifier.send_message("SSL证书状态通知", message)
        print("\n✅ 所有SSL证书检查通过，状态正常")