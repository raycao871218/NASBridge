#!/usr/bin/env python3
"""
本机用
Hosts文件智能更新脚本
根据NAS和OpenWrt的网络连通性动态调整域名解析
优先级：NAS > OpenWrt > 注释
"""

import os
import sys
import subprocess
import platform
import logging
import socket
import ipaddress
from pathlib import Path
from dotenv import load_dotenv
from datetime import datetime

# 添加项目根目录到路径
project_root = Path(__file__).parent.parent


class HostsManager:
    def __init__(self):
        # 加载环境变量
        env_path = project_root / '.env'
        load_dotenv(env_path)
        
        # 配置参数
        self.domain_name = os.getenv('DOMAIN_NAME', '').strip('"')
        self.nas_ip = os.getenv('NAS_IP', '')
        self.openwrt_ip = os.getenv('OPENWRT_IP', '')
        self.local_ip_range = os.getenv('LOCAL_IP', '').strip('"')
        
        # 系统hosts文件路径
        self.hosts_file = self._get_hosts_file_path()
        
        # 配置日志
        self._setup_logging()
        
        # 验证配置
        self._validate_config()
    
    def _get_hosts_file_path(self):
        """获取系统hosts文件路径"""
        system = platform.system().lower()
        if system in ['linux', 'darwin']:  # Linux 或 macOS
            return '/etc/hosts'
        elif system == 'windows':
            return r'C:\Windows\System32\drivers\etc\hosts'
        else:
            raise OSError(f"不支持的操作系统: {system}")
    
    def _setup_logging(self):
        """配置日志系统"""
        log_dir = Path(os.getenv('LOG_DIR', project_root / 'log'))
        log_dir.mkdir(exist_ok=True)
        
        log_file = log_dir / 'hosts_update.log'
        
        logging.basicConfig(
            level=logging.INFO,
            format='%(asctime)s - %(levelname)s - %(message)s',
            handlers=[
                logging.FileHandler(log_file, encoding='utf-8'),
                logging.StreamHandler()
            ]
        )
        self.logger = logging.getLogger(__name__)
    
    def _validate_config(self):
        """验证配置参数"""
        if not self.domain_name:
            raise ValueError("DOMAIN_NAME 未配置")
        if not self.nas_ip:
            raise ValueError("NAS_IP 未配置")
        if not self.openwrt_ip:
            raise ValueError("OPENWRT_IP 未配置")
        if not self.local_ip_range:
            raise ValueError("LOCAL_IP 未配置")
        
        self.logger.info(f"配置加载完成 - 域名: {self.domain_name}, NAS: {self.nas_ip}, OpenWrt: {self.openwrt_ip}")
        self.logger.info(f"本地网段: {self.local_ip_range}")
    
    def is_ip_in_local_range(self, ip_address):
        """
        检查IP地址是否在本地网段内
        
        Args:
            ip_address: 要检查的IP地址
            
        Returns:
            bool: True表示在本地网段内，False表示不在
        """
        try:
            # 处理LOCAL_IP配置中的双斜杠问题
            local_range = self.local_ip_range.replace('//', '/')
            
            # 创建网络对象
            network = ipaddress.ip_network(local_range, strict=False)
            ip = ipaddress.ip_address(ip_address)
            
            is_in_range = ip in network
            self.logger.info(f"IP {ip_address} {'在' if is_in_range else '不在'} 本地网段 {local_range} 内")
            return is_in_range
            
        except Exception as e:
            self.logger.error(f"检查IP网段失败: {e}")
            return False
    
    def test_local_ip_functionality(self):
        """
        测试本地IP获取功能（原test_local_ip.py的功能）
        """
        self.logger.info("=== 测试本地IP获取功能 ===")
        
        local_ip = self.get_local_ip()
        if local_ip:
            self.logger.info(f"✅ 成功获取本机IP: {local_ip}")
            
            # 判断IP类型
            if local_ip.startswith('192.168.'):
                self.logger.info("📍 这是一个私有网络IP (192.168.x.x)")
            elif local_ip.startswith('10.'):
                self.logger.info("📍 这是一个私有网络IP (10.x.x.x)")
            elif local_ip.startswith('172.') and 16 <= int(local_ip.split('.')[1]) <= 31:
                self.logger.info("📍 这是一个私有网络IP (172.16-31.x.x)")
            else:
                self.logger.info("📍 这可能是一个公网IP或其他类型的IP")
            
            # 检查是否在配置的本地网段内
            in_local_range = self.is_ip_in_local_range(local_ip)
            if in_local_range:
                self.logger.info(f"📍 当前IP在配置的本地网段 {self.local_ip_range} 内")
            else:
                self.logger.info(f"📍 当前IP不在配置的本地网段 {self.local_ip_range} 内")
                
            return local_ip, in_local_range
        else:
            self.logger.error("❌ 无法获取本机IP地址")
            return None, False
    
    def get_local_ip(self):
        """
        获取本机局域网IP地址
        
        Returns:
            str: 本机局域网IP地址，获取失败返回None
        """
        try:
            # 方法1: 通过连接外部地址获取本机IP
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
                # 连接到一个外部地址（不实际发送数据）
                s.connect(("8.8.8.8", 80))
                local_ip = s.getsockname()[0]
                self.logger.info(f"获取本机IP地址: {local_ip}")
                return local_ip
        except Exception as e:
            self.logger.warning(f"方法1获取本机IP失败: {e}")
            
        try:
            # 方法2: 通过hostname获取
            hostname = socket.gethostname()
            local_ip = socket.gethostbyname(hostname)
            if local_ip != "127.0.0.1":
                self.logger.info(f"通过hostname获取本机IP: {local_ip}")
                return local_ip
        except Exception as e:
            self.logger.warning(f"方法2获取本机IP失败: {e}")
            
        try:
            # 方法3: 使用系统命令（macOS/Linux）
            if platform.system().lower() in ['darwin', 'linux']:
                # macOS: 使用route命令获取默认网关接口
                result = subprocess.run(
                    ['route', 'get', 'default'],
                    capture_output=True,
                    text=True,
                    timeout=5
                )
                
                if result.returncode == 0:
                    lines = result.stdout.split('\n')
                    interface = None
                    for line in lines:
                        if 'interface:' in line:
                            interface = line.split(':')[1].strip()
                            break
                    
                    if interface:
                        # 获取接口IP
                        result = subprocess.run(
                            ['ifconfig', interface],
                            capture_output=True,
                            text=True,
                            timeout=5
                        )
                        
                        if result.returncode == 0:
                            lines = result.stdout.split('\n')
                            for line in lines:
                                if 'inet ' in line and '127.0.0.1' not in line:
                                    parts = line.strip().split()
                                    for i, part in enumerate(parts):
                                        if part == 'inet' and i + 1 < len(parts):
                                            local_ip = parts[i + 1]
                                            self.logger.info(f"通过系统命令获取本机IP: {local_ip}")
                                            return local_ip
        except Exception as e:
            self.logger.warning(f"方法3获取本机IP失败: {e}")
        
        self.logger.error("无法获取本机IP地址")
        return None
    
    def ping_host(self, ip_address, timeout=3):
        """
        测试主机连通性
        
        Args:
            ip_address: 目标IP地址
            timeout: 超时时间（秒）
            
        Returns:
            bool: True表示可达，False表示不可达
        """
        try:
            system = platform.system().lower()
            
            if system in ['linux', 'darwin']:  # Linux 或 macOS
                cmd = ['ping', '-c', '1', '-W', str(timeout * 1000), ip_address]
            elif system == 'windows':
                cmd = ['ping', '-n', '1', '-w', str(timeout * 1000), ip_address]
            else:
                self.logger.error(f"不支持的操作系统: {system}")
                return False
            
            result = subprocess.run(
                cmd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=timeout + 1
            )
            
            is_reachable = result.returncode == 0
            status = "可达" if is_reachable else "不可达"
            self.logger.info(f"网络测试 {ip_address}: {status}")
            
            return is_reachable
            
        except subprocess.TimeoutExpired:
            self.logger.warning(f"Ping {ip_address} 超时")
            return False
        except Exception as e:
            self.logger.error(f"Ping {ip_address} 出错: {e}")
            return False
    
    def read_hosts_file(self):
        """读取hosts文件内容"""
        try:
            with open(self.hosts_file, 'r', encoding='utf-8') as f:
                return f.readlines()
        except UnicodeDecodeError:
            # 尝试其他编码
            with open(self.hosts_file, 'r', encoding='gbk') as f:
                return f.readlines()
    
    def write_hosts_file(self, lines):
        """写入hosts文件"""
        try:
            with open(self.hosts_file, 'w', encoding='utf-8') as f:
                f.writelines(lines)
            return True
        except PermissionError:
            self.logger.error("权限不足，无法修改hosts文件。请使用管理员权限运行脚本。")
            return False
        except Exception as e:
            self.logger.error(f"写入hosts文件失败: {e}")
            return False
    
    def get_current_domain_status(self):
        """
        获取域名当前的解析状态
        
        Returns:
            tuple: (状态, IP地址)
            状态: 'nas', 'openwrt', 'commented', 'not_found'
        """
        lines = self.read_hosts_file()
        
        for line in lines:
            line = line.strip()
            
            # 检查激活的域名解析
            if line and not line.startswith('#') and self.domain_name in line:
                parts = line.split()
                if len(parts) >= 2 and parts[1] == self.domain_name:
                    ip = parts[0]
                    if ip == self.nas_ip:
                        return 'nas', ip
                    elif ip == self.openwrt_ip:
                        return 'openwrt', ip
                    else:
                        return 'other', ip
            
            # 检查被注释的域名解析
            if line.startswith('#') and self.domain_name in line:
                # 移除注释符号再检查
                uncommented = line[1:].strip()
                if uncommented:
                    parts = uncommented.split()
                    if len(parts) >= 2 and parts[1] == self.domain_name:
                        return 'commented', parts[0]
        
        return 'not_found', None
    
    def update_hosts_entry(self, target_ip=None, comment=False):
        """
        更新hosts文件中的域名解析
        
        Args:
            target_ip: 目标IP地址，None表示删除条目
            comment: 是否注释该条目
            
        Returns:
            bool: 操作是否成功
        """
        lines = self.read_hosts_file()
        new_lines = []
        found = False
        
        # 处理现有条目
        for line in lines:
            original_line = line
            line_stripped = line.strip()
            
            # 检查是否为目标域名的条目（包括注释的）
            is_target_line = False
            if line_stripped and self.domain_name in line_stripped:
                # 移除可能的注释符号
                uncommented = line_stripped.lstrip('#').strip()
                if uncommented:
                    parts = uncommented.split()
                    if len(parts) >= 2 and parts[1] == self.domain_name:
                        is_target_line = True
            
            if is_target_line:
                found = True
                if target_ip:
                    # 更新或添加条目
                    prefix = '# ' if comment else ''
                    new_line = f"{prefix}{target_ip}\t{self.domain_name}\n"
                    new_lines.append(new_line)
                # 如果target_ip为None，则跳过该行（删除条目）
            else:
                new_lines.append(original_line)
        
        # 如果没有找到现有条目且需要添加新条目
        if not found and target_ip:
            prefix = '# ' if comment else ''
            new_line = f"{prefix}{target_ip}\t{self.domain_name}\n"
            new_lines.append(new_line)
        
        # 写入文件
        return self.write_hosts_file(new_lines)
    
    def run(self):
        """执行主要逻辑"""
        self.logger.info("开始执行hosts文件更新检查")
        
        # 获取并测试本机IP
        local_ip, in_local_range = self.test_local_ip_functionality()
        
        if not local_ip:
            self.logger.error("无法获取本机IP，退出程序")
            return
        
        # 如果本机IP在LOCAL_IP网段内，直接注释hosts解析
        if in_local_range:
            self.logger.info("本机IP在本地网段内，直接注释域名解析")
            current_status, current_ip = self.get_current_domain_status()
            
            if current_status != 'commented':
                self.logger.info(f"将域名 {self.domain_name} 的解析注释掉")
                success = self.update_hosts_entry(current_ip or self.nas_ip, comment=True)
                
                if success:
                    self.logger.info("Hosts文件更新成功 - 已注释域名解析")
                else:
                    self.logger.error("Hosts文件更新失败")
            else:
                self.logger.info(f"域名 {self.domain_name} 已经是注释状态，无需更新")
            
            return
        
        # 如果不在本地网段，执行原有的网络连通性检查逻辑
        self.logger.info("本机IP不在本地网段内，执行网络连通性检查")
        
        # 测试网络连通性
        nas_reachable = self.ping_host(self.nas_ip)
        openwrt_reachable = self.ping_host(self.openwrt_ip)
        
        # 获取当前状态
        current_status, current_ip = self.get_current_domain_status()
        
        # 决定目标状态
        target_ip = None
        target_status = None
        
        if nas_reachable:
            target_ip = self.nas_ip
            target_status = 'nas'
        elif openwrt_reachable:
            target_ip = self.openwrt_ip
            target_status = 'openwrt'
        else:
            target_status = 'commented'
        
        # 检查是否需要更新
        need_update = False
        action_description = ""
        
        if target_status == 'commented':
            if current_status != 'commented':
                need_update = True
                action_description = f"网络不可达，注释域名解析 {self.domain_name}"
        else:
            if current_status != target_status:
                need_update = True
                if current_status == 'not_found':
                    action_description = f"添加域名解析: {self.domain_name} -> {target_ip} ({target_status.upper()})"
                else:
                    action_description = f"切换域名解析: {self.domain_name} {current_ip} -> {target_ip} ({target_status.upper()})"
        
        # 执行更新
        if need_update:
            self.logger.info(f"执行更新: {action_description}")
            
            if target_status == 'commented':
                success = self.update_hosts_entry(current_ip or self.nas_ip, comment=True)
            else:
                success = self.update_hosts_entry(target_ip, comment=False)
            
            if success:
                self.logger.info("Hosts文件更新成功")
            else:
                self.logger.error("Hosts文件更新失败")
        else:
            self.logger.info(f"无需更新，当前状态正确: {self.domain_name} -> {current_ip or '已注释'} ({current_status})")


def main():
    """主函数"""
    import argparse
    
    parser = argparse.ArgumentParser(description='Hosts文件智能更新脚本')
    parser.add_argument('--test-ip', action='store_true', help='仅测试本地IP获取功能')
    args = parser.parse_args()
    
    try:
        manager = HostsManager()
        
        if args.test_ip:
            # 仅测试IP功能
            manager.test_local_ip_functionality()
        else:
            # 执行完整的hosts更新逻辑
            manager.run()
            
    except Exception as e:
        logging.error(f"脚本执行失败: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()