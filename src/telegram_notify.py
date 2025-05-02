#!/usr/bin/env python3
import sys
import requests
import os
from dotenv import load_dotenv
from datetime import datetime
import glob

class TelegramNotifier:
    def __init__(self, bot_token, chat_id):
        self.bot_token = bot_token
        self.chat_id = chat_id
        self.api_url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
        self.base_url = f"https://api.telegram.org/bot{bot_token}"

    def test_connection(self):
        """
        测试与Telegram API的连接
        :return: (bool, str) - (是否连接成功, 错误信息)
        """
        try:
            # 使用getMe方法测试机器人API是否正常
            response = requests.get(f"{self.base_url}/getMe", timeout=10)
            response.raise_for_status()
            result = response.json()
            
            if result.get("ok"):
                bot_info = result.get("result", {})
                bot_name = bot_info.get("username", "Unknown")
                print(f"✓ Telegram API 连接成功！")
                print(f"✓ Bot名称: @{bot_name}")
                return True, None
            else:
                error_msg = result.get("description", "未知错误")
                return False, f"API返回错误：{error_msg}"
                
        except requests.exceptions.Timeout:
            return False, "连接超时，请检查网络状态"
        except requests.exceptions.RequestException as e:
            return False, f"连接错误：{str(e)}"
        except Exception as e:
            return False, f"未知错误：{str(e)}"

    def send_message(self, message, parse_mode="HTML"):
        """
        发送消息到Telegram
        :param message: 要发送的消息内容
        :param parse_mode: 消息解析模式（HTML或Markdown）
        :return: 是否发送成功
        """
        # 首先测试连接
        is_connected, error_msg = self.test_connection()
        if not is_connected:
            print(f"错误：无法连接到Telegram API")
            print(f"原因：{error_msg}")
            return False

        data = {
            "chat_id": self.chat_id,
            "text": message,
            "parse_mode": parse_mode
        }
        
        try:
            response = requests.post(self.api_url, data=data)
            response.raise_for_status()  # 检查是否有HTTP错误
            result = response.json()
            
            if result.get("ok"):
                print("消息发送成功！")
                return True
            else:
                print(f"消息发送失败：{result.get('description', '未知错误')}")
                return False
                
        except requests.exceptions.RequestException as e:
            print(f"发送消息时出错：{str(e)}")
            return False

def load_env_config():
    """
    从环境变量加载Telegram配置
    :return: (bot_token, chat_id, log_dir)
    """
    # 加载.env文件
    load_dotenv()
    
    bot_token = os.getenv('TELEGRAM_BOT_TOKEN')
    chat_id = os.getenv('TELEGRAM_USER_IDS')
    log_dir = os.getenv('LOG_DIR', '/var/log')  # 默认日志目录为/var/log
    
    if not bot_token or not chat_id:
        print("错误：环境变量中缺少必要的配置项")
        print("请确保在.env文件中设置了以下变量：")
        print("- TELEGRAM_BOT_TOKEN")
        print("- TELEGRAM_USER_IDS")
        print("- LOG_DIR (可选，默认为/var/log)")
        sys.exit(1)
        
    return bot_token, chat_id, log_dir

def find_log_file(log_dir, date_str=None):
    """
    根据日期查找日志文件
    :param log_dir: 日志目录
    :param date_str: 日期字符串 (YYYY-MM-DD)
    :return: 日志文件路径列表
    """
    if date_str:
        try:
            date_obj = datetime.strptime(date_str, '%Y-%m-%d')
            search_pattern = os.path.join(log_dir, f"*{date_str}*.log")
        except ValueError:
            print(f"错误：日期格式不正确，应为YYYY-MM-DD，例如：2024-03-20")
            sys.exit(1)
    else:
        # 如果没有指定日期，使用今天的日期
        date_str = datetime.now().strftime('%Y-%m-%d')
        search_pattern = os.path.join(log_dir, f"*{date_str}*.log")

    print(f"正在搜索日志文件...")
    print(f"日志目录: {log_dir}")
    print(f"搜索模式: {search_pattern}")
    
    # 检查目录是否存在
    if not os.path.exists(log_dir):
        print(f"错误：日志目录 {log_dir} 不存在！")
        sys.exit(1)
        
    # 列出目录中的所有文件
    print("\n目录中的文件：")
    for file in os.listdir(log_dir):
        print(f"- {file}")

    log_files = glob.glob(search_pattern)
    if not log_files:
        print(f"\n未找到{date_str}的日志文件")
        print("请确认：")
        print(f"1. 日志文件名中包含日期 {date_str}")
        print(f"2. 日志文件扩展名为 .log")
        print(f"3. 您有权限访问该目录和文件")
        sys.exit(1)
    
    return log_files

def read_log_content(log_files):
    """
    读取日志文件内容
    :param log_files: 日志文件路径列表
    :return: 日志内容
    """
    content = []
    for log_file in log_files:
        try:
            with open(log_file, 'r', encoding='utf-8') as f:
                content.append(f"=== {os.path.basename(log_file)} ===")
                content.append(f.read().strip())
        except Exception as e:
            print(f"读取日志文件 {log_file} 时出错：{str(e)}")
            continue
    
    return "\n\n".join(content) if content else "日志内容为空"

def main():
    # 检查命令行参数
    if len(sys.argv) < 2:
        print(f"使用方法: {sys.argv[0]} <消息内容>")
        print(f"或者: {sys.argv[0]} --log [YYYY-MM-DD]")
        sys.exit(1)
    
    # 加载配置
    bot_token, chat_id, log_dir = load_env_config()
    
    # 创建通知器实例
    notifier = TelegramNotifier(bot_token, chat_id)
    
    # 处理参数
    if sys.argv[1] == '--log':
        # 日志模式
        date_str = sys.argv[2] if len(sys.argv) > 2 else None
        log_files = find_log_file(log_dir, date_str)
        message = read_log_content(log_files)
    else:
        # 普通消息模式
        message = " ".join(sys.argv[1:])
    
    # 发送消息
    notifier.send_message(message)

if __name__ == "__main__":
    main() 