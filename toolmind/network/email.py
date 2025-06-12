import smtplib
import ssl
import logging
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.mime.application import MIMEApplication
from email.mime.image import MIMEImage
from email.header import Header
from email.utils import formataddr
from pathlib import Path
from typing import List, Dict, Optional, Union
import time
from dataclasses import dataclass
import re

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

@dataclass
class EmailConfig:
    """邮件配置类"""
    smtp_server: str
    smtp_port: int
    from_email: str
    login: str
    password: str
    use_tls: bool = True
    use_ssl: bool = False
    timeout: int = 30
    
    @classmethod
    def from_json_file(cls, file_path: str):
        """从JSON文件读取配置"""
        import json
        with open(file_path, 'r', encoding='utf-8') as f:
            config_data = json.load(f)
        return cls(**config_data)
    
    @classmethod
    def from_env_file(cls, file_path: str):
        """从.env文件读取配置"""
        config_data = {}
        with open(file_path, 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith('#') and '=' in line:
                    key, value = line.split('=', 1)
                    key = key.strip().lower()
                    value = value.strip().strip('"\'')
                    
                    # 转换数据类型
                    if key == 'smtp_port':
                        config_data[key] = int(value)
                    elif key == 'timeout':
                        config_data[key] = int(value)
                    elif key in ['use_tls', 'use_ssl']:
                        config_data[key] = value.lower() in ['true', '1', 'yes']
                    else:
                        config_data[key] = value
        
        return cls(**config_data)
    
    @classmethod
    def from_dict(cls, config_dict: dict):
        """从字典创建配置"""
        return cls(**config_dict)

class EmailSender:
    """增强的邮件发送类"""
    
    def __init__(self, config: EmailConfig):
        self.config = config
        self._validate_config()
    
    def _validate_config(self):
        """验证邮件配置"""
        if not self.config.smtp_server:
            raise ValueError("SMTP服务器地址不能为空")
        if not self.config.from_email:
            raise ValueError("发件人邮箱不能为空")
        if not self._is_valid_email(self.config.from_email):
            raise ValueError("发件人邮箱格式不正确")
    
    def _is_valid_email(self, email: str) -> bool:
        """验证邮箱格式"""
        pattern = r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$'
        return re.match(pattern, email) is not None
    
    def _validate_recipients(self, recipients: Union[str, List[str]]) -> List[str]:
        """验证收件人列表"""
        if isinstance(recipients, str):
            recipients = [recipients]
        
        valid_recipients = []
        for email in recipients:
            if self._is_valid_email(email):
                valid_recipients.append(email)
            else:
                logger.warning(f"无效的邮箱地址: {email}")
        
        if not valid_recipients:
            raise ValueError("没有有效的收件人邮箱")
        
        return valid_recipients
    
    def _create_message(self, 
                       subject: str, 
                       body: str, 
                       to_emails: List[str],
                       cc_emails: Optional[List[str]] = None,
                       bcc_emails: Optional[List[str]] = None,
                       html_body: Optional[str] = None,
                       from_name: Optional[str] = None) -> MIMEMultipart:
        """创建邮件消息"""
        msg = MIMEMultipart('alternative')
        
        # 设置发件人
        if from_name:
            msg['From'] = formataddr((Header(from_name, 'utf-8').encode(), self.config.from_email))
        else:
            msg['From'] = self.config.from_email
        
        # 设置收件人
        msg['To'] = ', '.join(to_emails)
        
        # 设置抄送
        if cc_emails:
            msg['Cc'] = ', '.join(cc_emails)
        
        # 设置主题
        msg['Subject'] = Header(subject, 'utf-8')
        
        # 添加纯文本正文
        text_part = MIMEText(body, 'plain', 'utf-8')
        msg.attach(text_part)
        
        # 添加HTML正文
        if html_body:
            html_part = MIMEText(html_body, 'html', 'utf-8')
            msg.attach(html_part)
        
        return msg
    
    def _add_attachments(self, msg: MIMEMultipart, attachments: List[Union[str, Path]]):
        """添加附件"""
        for attachment_path in attachments:
            try:
                path = Path(attachment_path)
                if not path.exists():
                    logger.warning(f"附件不存在: {path}")
                    continue
                
                with open(path, 'rb') as f:
                    if path.suffix.lower() in ['.jpg', '.jpeg', '.png', '.gif', '.bmp']:
                        # 图片附件
                        attachment = MIMEImage(f.read())
                    else:
                        # 普通附件
                        attachment = MIMEApplication(f.read())
                    
                    attachment.add_header(
                        'Content-Disposition',
                        'attachment',
                        filename=('utf-8', '', path.name)
                    )
                    msg.attach(attachment)
                    logger.info(f"已添加附件: {path.name}")
            
            except Exception as e:
                logger.error(f"添加附件失败 {attachment_path}: {e}")
    
    def _connect_smtp(self) -> smtplib.SMTP:
        """连接SMTP服务器"""
        try:
            if self.config.use_ssl:
                # 使用SSL连接
                context = ssl.create_default_context()
                server = smtplib.SMTP_SSL(
                    self.config.smtp_server, 
                    self.config.smtp_port, 
                    timeout=self.config.timeout,
                    context=context
                )
            else:
                # 使用普通连接
                server = smtplib.SMTP(
                    self.config.smtp_server, 
                    self.config.smtp_port, 
                    timeout=self.config.timeout
                )
                
                if self.config.use_tls:
                    server.starttls()  # 启用TLS加密
            
            # 登录SMTP服务器
            server.login(self.config.login, self.config.password)
            return server
            
        except smtplib.SMTPAuthenticationError:
            raise Exception("SMTP认证失败，请检查用户名和密码")
        except smtplib.SMTPConnectError:
            raise Exception(f"无法连接到SMTP服务器 {self.config.smtp_server}:{self.config.smtp_port}")
        except Exception as e:
            raise Exception(f"SMTP连接失败: {e}")
    
    def send_email(self,
                   subject: str,
                   body: str,
                   to_emails: Union[str, List[str]],
                   cc_emails: Optional[Union[str, List[str]]] = None,
                   bcc_emails: Optional[Union[str, List[str]]] = None,
                   html_body: Optional[str] = None,
                   attachments: Optional[List[Union[str, Path]]] = None,
                   from_name: Optional[str] = None,
                   retry_count: int = 3,
                   retry_delay: float = 1.0) -> Dict[str, any]:
        """
        发送邮件功能（增强版）
        
        :param subject: 邮件主题
        :param body: 邮件正文（纯文本）
        :param to_emails: 收件人邮箱（可以是字符串或列表）
        :param cc_emails: 抄送邮箱（可选）
        :param bcc_emails: 密送邮箱（可选）
        :param html_body: HTML格式邮件正文（可选）
        :param attachments: 附件列表（可选）
        :param from_name: 发件人姓名（可选）
        :param retry_count: 重试次数
        :param retry_delay: 重试间隔（秒）
        :return: 发送结果字典
        """
        result = {
            'success': False,
            'message': '',
            'sent_to': [],
            'failed_to': [],
            'attempt_count': 0
        }
        
        try:
            # 验证收件人
            to_emails = self._validate_recipients(to_emails)
            
            # 处理抄送和密送
            cc_emails = self._validate_recipients(cc_emails) if cc_emails else []
            bcc_emails = self._validate_recipients(bcc_emails) if bcc_emails else []
            
            # 创建邮件消息
            msg = self._create_message(
                subject, body, to_emails, cc_emails, bcc_emails, html_body, from_name
            )
            
            # 添加附件
            if attachments:
                self._add_attachments(msg, attachments)
            
            # 所有收件人（包括抄送和密送）
            all_recipients = to_emails + cc_emails + bcc_emails
            
            # 重试发送
            for attempt in range(retry_count):
                result['attempt_count'] = attempt + 1
                try:
                    server = self._connect_smtp()
                    
                    try:
                        # 发送邮件
                        failed_recipients = server.sendmail(
                            self.config.from_email, 
                            all_recipients, 
                            msg.as_string()
                        )
                        
                        # 处理发送结果
                        if failed_recipients:
                            result['failed_to'] = list(failed_recipients.keys())
                            result['sent_to'] = [email for email in all_recipients 
                                               if email not in failed_recipients]
                            result['message'] = f"部分收件人发送失败: {failed_recipients}"
                            logger.warning(result['message'])
                        else:
                            result['sent_to'] = all_recipients
                            result['success'] = True
                            result['message'] = f"邮件发送成功，共发送给 {len(all_recipients)} 个收件人"
                            logger.info(result['message'])
                        
                        break  # 发送成功，跳出重试循环
                        
                    finally:
                        server.quit()
                
                except Exception as e:
                    error_msg = f"第 {attempt + 1} 次发送失败: {e}"
                    logger.error(error_msg)
                    
                    if attempt < retry_count - 1:  # 还有重试机会
                        logger.info(f"等待 {retry_delay} 秒后重试...")
                        time.sleep(retry_delay)
                        retry_delay *= 2  # 指数退避
                    else:
                        result['message'] = f"邮件发送失败，已重试 {retry_count} 次: {e}"
            
        except Exception as e:
            result['message'] = f"邮件发送错误: {e}"
            logger.error(result['message'])
        
        return result
    
    def send_bulk_emails(self, 
                        email_list: List[Dict],
                        batch_size: int = 10,
                        delay_between_batches: float = 1.0) -> Dict[str, any]:
        """
        批量发送邮件
        
        :param email_list: 邮件列表，每个元素是包含邮件信息的字典
        :param batch_size: 批处理大小
        :param delay_between_batches: 批次间延迟
        :return: 批量发送结果
        """
        results = {
            'total': len(email_list),
            'success_count': 0,
            'failed_count': 0,
            'results': []
        }
        
        for i in range(0, len(email_list), batch_size):
            batch = email_list[i:i + batch_size]
            logger.info(f"发送批次 {i//batch_size + 1}，包含 {len(batch)} 封邮件")
            
            for email_info in batch:
                result = self.send_email(**email_info)
                results['results'].append(result)
                
                if result['success']:
                    results['success_count'] += 1
                else:
                    results['failed_count'] += 1
            
            # 批次间延迟
            if i + batch_size < len(email_list):
                time.sleep(delay_between_batches)
        
        logger.info(f"批量发送完成: 成功 {results['success_count']}, 失败 {results['failed_count']}")
        return results

# 便捷函数，保持向后兼容
def send_email(subject, body, to_email, from_email, smtp_server, smtp_port, login, password):
    """
    简单的邮件发送函数（向后兼容）
    """
    config = EmailConfig(
        smtp_server=smtp_server,
        smtp_port=smtp_port,
        from_email=from_email,
        login=login,
        password=password
    )
    
    sender = EmailSender(config)
    result = sender.send_email(subject, body, to_email)
    
    if result['success']:
        print("邮件发送成功")
    else:
        print(f"邮件发送失败: {result['message']}")
