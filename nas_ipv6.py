import re, os
import subprocess
from dotenv import load_dotenv

# 脚本功能说明：
# 此脚本用于自动更新防火墙规则中的IPv6地址。在以下场景中特别有用：
# 1. 使用DDNS（动态域名解析）+ IPv6地址的网络环境
# 2. 由于IPv6地址是动态分配的，需要定期更新防火墙规则
# 3. 适用于没有固定IPv4服务器的环境
# 4. 通过环境变量配置所需参数，支持灵活配置

# 所需环境变量说明：
# - IPV6_SUFFIX: IPv6地址的后缀部分
# - PORTS_TO_CHECK: 需要检查的端口列表，以逗号分隔
# - FIREWALL_CONFIG_PATH: 防火墙配置文件的路径
# - FIREWALL_RESTART_CMD: 更新配置后重启防火墙的命令

# 加载环境变量配置
load_dotenv()

# 终端输出图形配置
ERROR_ICON = '''
  ❌
  [ERROR]
'''

SUCCESS_ICON = '''
  ✅
  [SUCCESS]
'''

WARNING_ICON = '''
  ⚠️
  [WARNING]
'''

def get_ipv6_public_address():
    """获取系统当前的IPv6公网地址
    
    通过执行系统命令 'ip -6 addr show scope global' 获取IPv6地址信息
    从输出结果中解析出第一个全局范围的IPv6地址
    
    Returns:
        str or None: 成功返回IPv6地址字符串，失败返回None
    """
    try:
        output = subprocess.check_output(["ip", "-6", "addr", "show", "scope", "global"]).decode()
        lines = output.split('\n')
        for line in lines:
            if "inet6" in line:
                parts = line.split()
                address = parts[1].split('/')[0]  # 去除CIDR前缀长度部分
                return address
        return None
    except Exception as e:
        return None

def combine_ipv6_addresses(original_ip, suffix):
    """组合IPv6地址前缀和后缀
    
    Args:
        original_ip (str): 原始IPv6地址，用于提取前缀部分
        suffix (str): 要附加的IPv6地址后缀
    
    Returns:
        str: 组合后的完整IPv6地址
    
    说明：
        - 处理带有'::'和不带'::'的IPv6地址格式
        - 确保生成正确的IPv6地址格式
    """
    parts = original_ip.split('::')
    if len(parts) == 1:  # 不包含'::'的情况
        prefix = parts[0]
    else:  # 包含'::'的情况
        prefix = parts[0] + ':'
    new_ip = f"{prefix}{suffix}"
    return new_ip

# 获取当前IPv6地址并与配置的后缀组合
ipv6_address = get_ipv6_public_address()
suffix = os.getenv('IPV6_SUFFIX')
if ipv6_address and suffix:
    specified_ipv6 = combine_ipv6_addresses(ipv6_address, suffix)
else:
    print(f"{ERROR_ICON}未找到 IPv6 公网地址或后缀配置，无法组合。")
    exit(1)

# 从环境变量获取需要检查的端口列表
ports_str = os.getenv('PORTS_TO_CHECK')
ports_to_check = [int(port) for port in ports_str.split(',')] if ports_str else []

# 获取防火墙配置文件路径
config_file = os.getenv('FIREWALL_CONFIG_PATH')
if not config_file:
    print(f"{ERROR_ICON}未找到防火墙配置文件路径配置。")
    exit(1)

# 终端输出颜色配置
green = '\033[0;32m'   # 绿色，用于成功信息
yellow = '\033[1;33m'  # 黄色，用于警告信息
reset = '\033[0m'      # 重置颜色

# 统计跳过的规则数量
skipped_rules = 0

def parse_block(block):
    """解析防火墙规则块，提取关键配置信息
    
    Args:
        block (list): 包含规则配置行的列表
    
    Returns:
        tuple: (规则名称, 源端口, 目标端口, 源IP, 目标IP)
        所有值都可能为None，表示未在规则中找到对应配置
    
    说明：
        - 使用正则表达式匹配各种配置选项
        - 支持解析name、src_dport、dest_port、src、dest_ip等配置
    """
    rule_name = None
    src_dport = None
    dest_port = None
    src_ip = None
    dest_ip = None

    for line in block:
        # 解析规则名称
        name_match = re.search(r"option name '([^']+)'", line)
        if name_match:
            rule_name = name_match.group(1)

        # 解析源端口配置
        dport_match = re.search(r"option src_dport '([^']+)'", line)
        if dport_match:
            src_dport = int(dport_match.group(1))

        # 解析目标端口配置
        dest_port_match = re.search(r"option dest_port '([^']+)'", line)
        if dest_port_match:
            dest_port = int(dest_port_match.group(1))

        # 解析源IP配置
        src_ip_match = re.search(r"option src '([^']+)'", line)
        if src_ip_match:
            src_ip = src_ip_match.group(1)

        # 解析目标IP配置（支持list和option两种格式）
        dest_ip_match = re.search(r"(list dest_ip|option dest_ip) '([^']+)'", line)
        if dest_ip_match:
            dest_ip = dest_ip_match.group(2)

    return rule_name, src_dport, dest_port, src_ip, dest_ip

# 读取防火墙配置文件
try:
    with open(config_file, "r") as f:
        config_data = f.readlines()
except Exception as e:
    print(f"{ERROR_ICON}无法读取配置文件：{e}")
    exit(1)

# 用于存储更新后的配置内容
updated_lines = []

# 规则块处理相关变量
current_block = []  # 当前正在处理的规则块内容
in_block = False    # 是否正在处理规则块
changes_made = False  # 是否有修改发生

# 遍历配置文件的每一行
for line in config_data:
    # 检测新的配置块开始
    if re.match(r"^config (rule|redirect)", line):
        if in_block:
            # 处理当前已完成的配置块
            block_str = ''.join(current_block)
            matched = False

            # 检查配置块是否包含需要处理的端口
            for port in ports_to_check:
                # 匹配指定端口的IPv6规则
                if (f"option src_dport '{port}'" in block_str or f"option dest_port '{port}'" in block_str) and "option family 'ipv6'" in block_str:
                    matched = True
                    rule_name, src_dport, dest_port, src_ip, dest_ip = parse_block(current_block)

                    # 检查并更新目标IP地址
                    if rule_name and (src_dport or dest_port) and dest_ip:
                        if dest_ip != specified_ipv6:
                            print(f"{WARNING_ICON}{yellow}{rule_name},\n{src_dport or dest_port},\n{src_ip},\n{dest_ip} (Change needed){reset}")
                            # 更新目标IP地址
                            block_str = re.sub(dest_ip, specified_ipv6, block_str)
                            changes_made = True
                    break

            # 保存处理后的配置块
            updated_lines.append(block_str)
            if not matched:
                skipped_rules += 1
        else:
            updated_lines.append(''.join(current_block))

        # 开始新的配置块
        current_block = [line]
        in_block = True
    else:
        # 继续收集当前配置块的内容
        current_block.append(line)

# 处理文件的最后一个配置块
if in_block:
    block_str = ''.join(current_block)
    matched = False

    # 检查最后一个配置块
    for port in ports_to_check:
        if (f"option src_dport '{port}'" in block_str or f"option dest_port '{port}'" in block_str) and "option family 'ipv6'" in block_str:
            matched = True
            rule_name, src_dport, dest_port, src_ip, dest_ip = parse_block(current_block)

            if rule_name and (src_dport or dest_port) and dest_ip:
                if dest_ip != specified_ipv6:
                    print(f"{WARNING_ICON}{yellow}{rule_name},\n{src_dport or dest_port},\n{src_ip},\n{dest_ip} (Change needed){reset}")
                    block_str = re.sub(dest_ip, specified_ipv6, block_str)
                    changes_made = True
            break

    updated_lines.append(block_str)
    if not matched:
        skipped_rules += 1

# 如果有修改，更新配置文件并重启防火墙
if changes_made:
    try:
        # 写入更新后的配置
        with open(config_file, "w") as f:
            f.writelines(updated_lines)
        print(f"{SUCCESS_ICON}Config file has been updated with new IPv6 addresses.")
        
        # 执行防火墙重启命令
        restart_cmd = os.getenv('FIREWALL_RESTART_CMD')
        if restart_cmd:
            os.system(restart_cmd)
        else:
            print(f"{WARNING_ICON}未找到防火墙重启命令配置。")
    except Exception as e:
        print(f"{ERROR_ICON}更新配置文件时发生错误：{e}")