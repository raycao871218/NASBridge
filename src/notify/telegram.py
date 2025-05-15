#!/usr/bin/env python3
import requests
import os
from dotenv import load_dotenv

class TelegramNotifier:
    def __init__(self, bot_token=None, chat_id=None):
        """
        初始化Telegram通知器
        :param bot_token: Telegram Bot Token，如果为None则从环境变量读取
        :param chat_id: 接收消息的Chat ID，如果为None则从环境变量读取
        """
        if bot_token is None or chat_id is None:
            load_dotenv()
            bot_token = bot_token or os.getenv('TELEGRAM_BOT_TOKEN')
            chat_id = chat_id or os.getenv('TELEGRAM_USER_IDS')
            
            if not bot_token or not chat_id:
                raise ValueError("请在环境变量或初始化参数中设置TELEGRAM_BOT_TOKEN和TELEGRAM_USER_IDS")
        
        self.bot_token = bot_token
        self.chat_id = chat_id
        self.api_url = f"https://api.telegram.org/bot{bot_token}/sendMessage"

    def send_message(self, message, parse_mode="HTML", silent=False):
        """
        发送消息到Telegram
        :param message: 要发送的消息内容
        :param parse_mode: 消息解析模式（HTML或Markdown）
        :param silent: 是否静默发送（不发出通知声音）
        :return: (bool, str) - (是否发送成功, 错误信息)
        """
        data = {
            "chat_id": self.chat_id,
            "text": message,
            "parse_mode": parse_mode,
            "disable_notification": silent
        }
        
        try:
            response = requests.post(self.api_url, data=data, timeout=10)
            response.raise_for_status()
            result = response.json()
            
            if result.get("ok"):
                return True, None
            else:
                error_msg = result.get("description", "未知错误")
                return False, f"API返回错误：{error_msg}"
                
        except requests.exceptions.Timeout:
            return False, "连接超时，请检查网络状态"
        except requests.exceptions.RequestException as e:
            return False, f"请求错误：{str(e)}"
        except Exception as e:
            return False, f"未知错误：{str(e)}"