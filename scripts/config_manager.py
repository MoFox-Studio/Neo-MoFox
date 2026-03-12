import tkinter as tk
from tkinter import ttk, messagebox, filedialog
import tomli
import tomli_w
import os

class ConfigManager:
    """模型配置管理器"""
    def __init__(self, root):
        self.root = root
        self.root.title("模型配置管理器")
        self.root.geometry("800x600")
        
        self.config_data = None
        self.current_file = None
        self.last_file_path = self.load_last_file_path()
        
        self.create_widgets()
        if self.last_file_path and os.path.exists(self.last_file_path):
            self.open_file(self.last_file_path)
        else:
            self.load_default_config()
            
        # 绑定关闭事件
        self.root.protocol("WM_DELETE_WINDOW", self.on_closing)
        
    def load_last_file_path(self):
        """加载上次打开的文件路径"""
        config_dir = os.path.join(os.path.expanduser("~"), ".maibot")
        config_file = os.path.join(config_dir, "config_manager.conf")
        try:
            if os.path.exists(config_file):
                with open(config_file, "r", encoding="utf-8") as f:
                    return f.read().strip()
        except Exception:
            pass
        return None
        
    def save_last_file_path(self):
        """保存最后打开的文件路径"""
        if self.current_file:
            config_dir = os.path.join(os.path.expanduser("~"), ".maibot")
            os.makedirs(config_dir, exist_ok=True)
            config_file = os.path.join(config_dir, "config_manager.conf")
            try:
                with open(config_file, "w", encoding="utf-8") as f:
                    f.write(self.current_file)
            except Exception:
                pass
    
    def on_closing(self):
        """处理窗口关闭事件"""
        if self.is_modified():
            if messagebox.askyesnocancel("保存更改", "是否保存更改？"):
                self.save_file()
                self.save_last_file_path()
                self.root.destroy()
            elif messagebox.askyesnocancel("保存更改", "是否不保存直接退出？") is True:
                self.save_last_file_path()
                self.root.destroy()
        else:
            self.save_last_file_path()
            self.root.destroy()
    
    def create_widgets(self):
        # Menu bar
        menubar = tk.Menu(self.root)
        file_menu = tk.Menu(menubar, tearoff=0)
        file_menu.add_command(label="打开", command=self.open_file)
        file_menu.add_command(label="保存", command=self.save_file)
        file_menu.add_command(label="另存为", command=self.save_as_file)
        file_menu.add_separator()
        file_menu.add_command(label="退出", command=self.root.quit)
        menubar.add_cascade(label="文件", menu=file_menu)
        self.root.config(menu=menubar)
        
        # Main notebook
        self.notebook = ttk.Notebook(self.root)
        self.notebook.pack(fill=tk.BOTH, expand=True)
        
        # API Providers tab
        self.api_frame = ttk.Frame(self.notebook)
        self.notebook.add(self.api_frame, text="API提供商")
        self.create_api_providers_tab()
        
        # Models tab
        self.models_frame = ttk.Frame(self.notebook)
        self.notebook.add(self.models_frame, text="模型")
        self.create_models_tab()
        
        # Model Tasks tab
        self.tasks_frame = ttk.Frame(self.notebook)
        self.notebook.add(self.tasks_frame, text="模型任务") 
        self.create_tasks_tab()
        
        # Status bar
        self.status_var = tk.StringVar()
        self.status_var.set(f"当前文件: {self.current_file}" if self.current_file else "未加载文件")
        status_bar = ttk.Label(self.root, textvariable=self.status_var, relief=tk.SUNKEN, anchor=tk.W)
        status_bar.pack(side=tk.BOTTOM, fill=tk.X)
    
    def create_api_providers_tab(self):
        # Treeview for API providers
        self.api_tree = ttk.Treeview(self.api_frame, columns=("name", "base_url", "client_type"), show="headings")
        self.api_tree.heading("name", text="名称")
        self.api_tree.heading("base_url", text="基础URL")
        self.api_tree.heading("client_type", text="客户端类型")
        # 配置错误标签样式
        self.api_tree.tag_configure('error', foreground='red')  # 整行标红
        self.api_tree.tag_configure('error_field', background='#FFE0E0')  # 字段背景浅红
        self.api_tree.tag_configure('error_value', foreground='red', background='#FFE0E0')  # 错误值标红+背景
        self.api_tree.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)
        self.api_tree.bind("<Double-1>", lambda e: self.edit_api_provider())
        
        # Buttons frame
        btn_frame = ttk.Frame(self.api_frame)
        btn_frame.pack(fill=tk.X, padx=5, pady=5)
        
        ttk.Button(btn_frame, text="添加提供商", command=self.add_api_provider).pack(side=tk.LEFT, padx=2)
        ttk.Button(btn_frame, text="编辑提供商", command=self.edit_api_provider).pack(side=tk.LEFT, padx=2)
        ttk.Button(btn_frame, text="删除提供商", command=self.remove_api_provider).pack(side=tk.LEFT, padx=2)
    
    def create_models_tab(self):
        # Treeview for models
        self.models_tree = ttk.Treeview(
            self.models_frame,
            columns=("name", "identifier", "provider", "max_context", "tool_call_compat", "price_in", "price_out"),
            show="headings"
        )
        self.models_tree.heading("name", text="名称")
        self.models_tree.heading("identifier", text="标识符")
        self.models_tree.heading("provider", text="提供商")
        self.models_tree.heading("max_context", text="最大上下文")
        self.models_tree.heading("tool_call_compat", text="Tool兼容")
        self.models_tree.heading("price_in", text="输入价格")
        self.models_tree.heading("price_out", text="输出价格")
        # 配置错误标签样式
        self.models_tree.tag_configure('error', foreground='red')  # 整行标红
        self.models_tree.tag_configure('error_field', background='#FFE0E0')  # 字段背景浅红
        self.models_tree.tag_configure('error_value', foreground='red', background='#FFE0E0')  # 错误值标红+背景
        self.models_tree.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)
        self.models_tree.bind("<Double-1>", lambda e: self.edit_model())
        
        # Buttons frame
        btn_frame = ttk.Frame(self.models_frame)
        btn_frame.pack(fill=tk.X, padx=5, pady=5)
        
        ttk.Button(btn_frame, text="添加模型", command=self.add_model).pack(side=tk.LEFT, padx=2)
        ttk.Button(btn_frame, text="编辑模型", command=self.edit_model).pack(side=tk.LEFT, padx=2)
        ttk.Button(btn_frame, text="删除模型", command=self.remove_model).pack(side=tk.LEFT, padx=2)
    
    def create_tasks_tab(self):
        # Treeview for model tasks
        self.tasks_tree = ttk.Treeview(self.tasks_frame, columns=("task", "models", "temperature", "max_tokens"), show="headings")
        self.tasks_tree.heading("task", text="任务")
        self.tasks_tree.heading("models", text="模型列表")
        self.tasks_tree.heading("temperature", text="温度")
        self.tasks_tree.heading("max_tokens", text="最大Token数")
        # 配置错误标签样式
        self.tasks_tree.tag_configure('error', foreground='red')  # 整行标红
        self.tasks_tree.tag_configure('error_field', background='#FFE0E0')  # 字段背景浅红
        self.tasks_tree.tag_configure('error_value', foreground='red', background='#FFE0E0')  # 错误值标红+背景
        self.tasks_tree.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)
        self.tasks_tree.bind("<Double-1>", lambda e: self.edit_task())
        
        # Buttons frame
        btn_frame = ttk.Frame(self.tasks_frame)
        btn_frame.pack(fill=tk.X, padx=5, pady=5)
        
        ttk.Button(btn_frame, text="编辑任务", command=self.edit_task).pack(side=tk.LEFT, padx=2)
    
    def load_default_config(self):
        # Initialize empty config structure
        self.config_data = {
            "api_providers": [],
            "models": [],
            "model_tasks": {}
        }
        self.current_file = None
        self.update_ui()
    
    def open_file(self, file_path=None):
        if file_path is None:
            file_path = filedialog.askopenfilename(
                filetypes=[("TOML files", "*.toml"), ("All files", "*.*")],
                initialdir=os.path.dirname(self.current_file) if self.current_file else os.getcwd()
            )
        if file_path:
            try:
                with open(file_path, "rb") as f:
                    self.config_data = tomli.load(f)
                
                # Validate required sections
                required_sections = ["api_providers", "models", "model_tasks"]
                missing_sections = [section for section in required_sections 
                                  if section not in self.config_data]
                
                if missing_sections:
                    messagebox.showwarning(
                        "警告", 
                        f"配置文件缺少必要部分: {', '.join(missing_sections)}\n"
                        "这可能导致配置不完整。"
                    )
                
                self.current_file = file_path
                self.update_ui()
            except tomli.TOMLDecodeError as e:
                messagebox.showerror("格式错误", f"TOML文件格式不正确: {str(e)}")
            except Exception as e:
                messagebox.showerror("错误", f"加载文件失败: {str(e)}")
    
    def save_file(self):
        if not self.current_file:
            self.save_as_file()
            return
        
        # 在保存前验证配置
        if not self.validate_config():
            if not messagebox.askyesno("配置验证", "配置中存在问题，是否仍然保存？"):
                return
        
        try:
            with open(self.current_file, "wb") as f:
                tomli_w.dump(self.config_data, f)
            self.update_status()
            messagebox.showinfo("成功", "文件保存成功")
        except Exception as e:
            messagebox.showerror("错误", f"保存文件失败: {str(e)}")
    
    def save_as_file(self):
        file_path = filedialog.asksaveasfilename(
            defaultextension=".toml",
            filetypes=[("TOML files", "*.toml"), ("All files", "*.*")],
            initialdir=os.getcwd()
        )
        if file_path:
            self.current_file = file_path
            self.update_status()
            self.save_file()
    
    def update_status(self):
        if self.current_file:
            # Show relative path if file is in current directory
            try:
                rel_path = os.path.relpath(self.current_file)
                status = f"当前文件: {rel_path}"
            except ValueError:
                status = f"当前文件: {self.current_file}"
            
            # Add modified state
            if self.is_modified():
                status += " (已修改)"
            
            # Add models/tasks count
            model_count = len(self.config_data.get("models", []))
            task_count = len(self.config_data.get("model_tasks", {}))
            status += f" | 模型: {model_count} | 任务: {task_count}"
            
            self.status_var.set(status)
        else:
            self.status_var.set("未加载文件 | 请打开或创建配置文件")

    def is_modified(self):
        """Check if current config differs from saved file"""
        if not self.current_file or not os.path.exists(self.current_file):
            return True
            
        try:
            with open(self.current_file, "rb") as f:
                saved_data = tomli.load(f)
            return saved_data != self.config_data
        except Exception:
            return True

    def validate_config(self):
        """验证配置的合法性并高亮问题项"""
        # 清除所有已有的标签
        for tree in [self.api_tree, self.models_tree, self.tasks_tree]:
            tree.tag_configure('error', foreground='red')
            tree.tag_configure('error_field', background='#FFE0E0')  # 浅红色背景
            for item in tree.get_children():
                tree.item(item, tags=())
        
        # 获取所有提供商名称和详细信息
        provider_info = {p["name"]: p for p in self.config_data.get("api_providers", [])}
        provider_names = set(provider_info.keys())
        
        # 获取所有模型名称和详细信息
        model_info = {m["name"]: m for m in self.config_data.get("models", [])}
        
        errors = []
        problematic_items = {
            'providers': {},  # 存储有问题的提供商及其错误字段
            'models': {},     # 存储有问题的模型及其错误字段
            'tasks': {}       # 存储有问题的任务及其错误字段
        }
        
        # 验证提供商配置
        for provider in self.config_data.get("api_providers", []):
            name = provider.get("name", "")
            if not name:
                continue  # 跳过没有名称的提供商
            
            error_fields = []
            if not provider.get("base_url"):
                error_fields.append(1)  # base_url列索引
            if not provider.get("api_key"):
                error_fields.append(2)  # api_key列索引
                
            if error_fields:
                problematic_items['providers'][name] = error_fields
                errors.append(f"提供商 '{name}' 缺少必要配置")
        
        # 验证模型配置
        for model in self.config_data.get("models", []):
            name = model.get("name", "")
            if not name:
                continue
                
            error_fields = []
            if not model.get("model_identifier"):
                error_fields.append(1)  # model_identifier列索引
            
            provider = model.get("api_provider")
            if not provider or provider not in provider_names:
                error_fields.append(2)  # provider列索引
                errors.append(f"模型 '{name}' 使用了不存在的提供商 '{provider}'")

            max_context = model.get("max_context")
            if max_context is not None and (not isinstance(max_context, int) or max_context <= 0):
                error_fields.append(3)  # max_context列索引
                errors.append(f"模型 '{name}' 的max_context必须是大于0的整数")

            tool_call_compat = model.get("tool_call_compat")
            if tool_call_compat is not None and not isinstance(tool_call_compat, bool):
                error_fields.append(4)  # tool_call_compat列索引
                errors.append(f"模型 '{name}' 的tool_call_compat必须是布尔值")

            extra_params = model.get("extra_params")
            if extra_params is not None:
                if not isinstance(extra_params, dict):
                    error_fields.append(5)
                    errors.append(f"模型 '{name}' 的extra_params必须是字典")
                else:
                    reserve_ratio = extra_params.get("context_reserve_ratio")
                    if reserve_ratio is not None and not isinstance(reserve_ratio, (int, float)):
                        error_fields.append(5)
                        errors.append(f"模型 '{name}' 的extra_params.context_reserve_ratio必须是数字")

                    reserve_tokens = extra_params.get("context_reserve_tokens")
                    if reserve_tokens is not None and not isinstance(reserve_tokens, int):
                        error_fields.append(5)
                        errors.append(f"模型 '{name}' 的extra_params.context_reserve_tokens必须是整数")
            
            if error_fields:
                problematic_items['models'][name] = error_fields
        
        # 验证任务配置
        for task_name, task_config in self.config_data.get("model_tasks", {}).items():
            error_fields = []
            model_list = task_config.get("model_list", [])
            if isinstance(model_list, str):
                model_list = [m.strip() for m in model_list.split(",")]
            
            # 检查模型列表
            invalid_models = []
            models_str = ", ".join(model_list)  # 当前显示的模型列表字符串
            for model_name in model_list:
                if model_name not in model_info:
                    invalid_models.append(model_name)
            
            if invalid_models:
                error_fields.append(1)  # models列索引
                # 为每个无效模型存储其在字符串中的位置
                problematic_items['tasks'][task_name] = {
                    'fields': error_fields,
                    'invalid_models': invalid_models,
                    'models_str': models_str
                }
                errors.append(f"任务 '{task_name}' 使用了不存在的模型: {', '.join(invalid_models)}")
            
            # 检查temperature值
            temp = task_config.get("temperature")
            if temp is not None and not isinstance(temp, (int, float)):
                error_fields.append(2)  # temperature列索引
                if task_name not in problematic_items['tasks']:
                    problematic_items['tasks'][task_name] = {
                        'fields': error_fields,
                        'invalid_temp': str(temp)
                    }
                else:
                    problematic_items['tasks'][task_name]['fields'] = error_fields
                    problematic_items['tasks'][task_name]['invalid_temp'] = str(temp)
                errors.append(f"任务 '{task_name}' 的temperature值必须是数字")
            
            # 检查max_tokens值
            tokens = task_config.get("max_tokens")
            if tokens is not None and (not isinstance(tokens, int) or tokens < 1 or tokens > 8000):
                error_fields.append(3)  # max_tokens列索引
                if task_name not in problematic_items['tasks']:
                    problematic_items['tasks'][task_name] = {
                        'fields': error_fields,
                        'invalid_tokens': str(tokens)
                    }
                else:
                    problematic_items['tasks'][task_name]['fields'] = error_fields
                    problematic_items['tasks'][task_name]['invalid_tokens'] = str(tokens)
                errors.append(f"任务 '{task_name}' 的max_tokens值无效 (应在1-8000之间)")
        
        # 高亮有问题的项目
        for item in self.api_tree.get_children():
            provider_name = self.api_tree.item(item)['values'][0]
            if provider_name in problematic_items['providers']:
                error_fields = problematic_items['providers'][provider_name]
                self.api_tree.item(item, tags=('error',))
                # 为特定字段添加error_field标签
                for field_idx in error_fields:
                    self.api_tree.set(item, field_idx, self.api_tree.item(item)['values'][field_idx])
                    self.api_tree.item(item, tags=('error_field',))
        
        for item in self.models_tree.get_children():
            model_name = self.models_tree.item(item)['values'][0]
            if model_name in problematic_items['models']:
                error_fields = problematic_items['models'][model_name]
                self.models_tree.item(item, tags=('error',))
                # 为特定字段添加error_field标签
                for field_idx in error_fields:
                    self.models_tree.set(item, field_idx, self.models_tree.item(item)['values'][field_idx])
                    self.models_tree.item(item, tags=('error_field',))
        
        for item in self.tasks_tree.get_children():
            task_name = self.tasks_tree.item(item)['values'][0]
            if task_name in problematic_items['tasks']:
                task_info = problematic_items['tasks'][task_name]
                error_fields = task_info.get('fields', [])
                self.tasks_tree.item(item, tags=('error',))
                
                # 处理模型列表中的错误
                if 'invalid_models' in task_info:
                    models_str = task_info['models_str']
                    invalid_models = task_info['invalid_models']
                    values = list(self.tasks_tree.item(item)['values'])
                    # 标记每个无效的模型名称
                    for invalid_model in invalid_models:
                        if invalid_model in models_str:
                            self.tasks_tree.set(item, 1, models_str)
                            self.tasks_tree.item(item, tags=('error', 'error_value'))
                
                # 处理temperature值错误
                if 'invalid_temp' in task_info:
                    self.tasks_tree.set(item, 2, task_info['invalid_temp'])
                    self.tasks_tree.item(item, tags=('error', 'error_value'))
                
                # 处理max_tokens值错误
                if 'invalid_tokens' in task_info:
                    self.tasks_tree.set(item, 3, task_info['invalid_tokens'])
                    self.tasks_tree.item(item, tags=('error', 'error_value'))
        
        if errors:
            messagebox.showwarning("配置验证警告", "发现以下问题：\n" + "\n".join(errors))
        
        return len(errors) == 0
    
    def update_ui(self):
        self.update_status()
        
        # 清空所有树视图
        for tree in [self.api_tree, self.models_tree, self.tasks_tree]:
            for item in tree.get_children():
                tree.delete(item)
        
        # 更新API提供商
        if "api_providers" in self.config_data:
            for provider in self.config_data["api_providers"]:
                name = provider.get("name", "")
                base_url = provider.get("base_url", "")
                client_type = provider.get("client_type", "openai")
                
                item = self.api_tree.insert("", tk.END, values=(name, base_url, client_type))
        
        # 更新模型
        if "models" in self.config_data:
            for model in self.config_data["models"]:
                name = model.get("name", "")
                identifier = model.get("model_identifier", "")
                provider = model.get("api_provider", "")
                max_context = model.get("max_context", 32768)
                tool_call_compat = model.get("tool_call_compat", False)
                price_in = model.get("price_in", 0)
                price_out = model.get("price_out", 0)
                
                item = self.models_tree.insert("", tk.END, values=(
                    name, identifier, provider, max_context, "是" if tool_call_compat else "否", price_in, price_out
                ))
        
        # 更新模型任务
        if "model_tasks" in self.config_data:
            for task_name, task_config in self.config_data["model_tasks"].items():
                model_list = task_config.get("model_list", [])
                if isinstance(model_list, str):
                    model_list = [m.strip() for m in model_list.split(",")]
                models = ", ".join(model_list)
                temp = task_config.get("temperature", "")
                tokens = task_config.get("max_tokens", "")
                
                item = self.tasks_tree.insert("", tk.END, values=(
                    task_name, models, temp, tokens
                ))
        
        # 在更新完UI后进行配置验证和错误高亮
        self.validate_config()
    
    def add_api_provider(self):
        self.edit_api_provider(is_new=True)
    
    def edit_api_provider(self, item=None, is_new=False):
        # If called from edit button, get selected item
        if not is_new and item is None:
            selected = self.api_tree.selection()
            if not selected:
                messagebox.showwarning("警告", "请选择要编辑的提供商")
                return
            item = selected[0]
            
        dialog = tk.Toplevel(self.root)
        dialog.title("编辑API提供商")
        dialog.transient(self.root)
        dialog.grab_set()
        
        # Form fields
        ttk.Label(dialog, text="名称:").grid(row=0, column=0, padx=5, pady=5, sticky=tk.E)
        name_entry = ttk.Entry(dialog)
        name_entry.grid(row=0, column=1, padx=5, pady=5, sticky=tk.W+tk.E)
        
        ttk.Label(dialog, text="基础URL:").grid(row=1, column=0, padx=5, pady=5, sticky=tk.E)
        url_entry = ttk.Entry(dialog)
        url_entry.grid(row=1, column=1, padx=5, pady=5, sticky=tk.W+tk.E)
        
        ttk.Label(dialog, text="API密钥:").grid(row=2, column=0, padx=5, pady=5, sticky=tk.E)
        key_entry = ttk.Entry(dialog)
        key_entry.grid(row=2, column=1, padx=5, pady=5, sticky=tk.W+tk.E)
        
        ttk.Label(dialog, text="客户端类型:").grid(row=3, column=0, padx=5, pady=5, sticky=tk.E)
        client_var = tk.StringVar(value="openai")
        ttk.Combobox(dialog, textvariable=client_var, values=["openai", "gemini"]).grid(row=3, column=1, padx=5, pady=5, sticky=tk.W+tk.E)
        
        ttk.Label(dialog, text="最大重试次数:").grid(row=4, column=0, padx=5, pady=5, sticky=tk.E)
        retry_entry = ttk.Entry(dialog)
        retry_entry.insert(0, "2")
        retry_entry.grid(row=4, column=1, padx=5, pady=5, sticky=tk.W+tk.E)
        
        ttk.Label(dialog, text="超时时间(秒):").grid(row=5, column=0, padx=5, pady=5, sticky=tk.E)
        timeout_entry = ttk.Entry(dialog)
        timeout_entry.insert(0, "30")
        timeout_entry.grid(row=5, column=1, padx=5, pady=5, sticky=tk.W+tk.E)
        
        ttk.Label(dialog, text="重试间隔(秒):").grid(row=6, column=0, padx=5, pady=5, sticky=tk.E)
        interval_entry = ttk.Entry(dialog)
        interval_entry.insert(0, "10")
        interval_entry.grid(row=6, column=1, padx=5, pady=5, sticky=tk.W+tk.E)
        
        # If editing, populate fields
        if item:
            values = self.api_tree.item(item, "values")
            name_entry.insert(0, values[0])
            url_entry.insert(0, values[1])
            client_var.set(values[2])
        
        # Buttons
        btn_frame = ttk.Frame(dialog)
        btn_frame.grid(row=7, column=0, columnspan=2, pady=10)
        
        def save_provider():
            provider = {
                "name": name_entry.get(),
                "base_url": url_entry.get(),
                "api_key": key_entry.get(),
                "client_type": client_var.get(),
                "max_retry": int(retry_entry.get()),
                "timeout": int(timeout_entry.get()),
                "retry_interval": int(interval_entry.get())
            }
            
            if "api_providers" not in self.config_data:
                self.config_data["api_providers"] = []
            
            if item:
                # Find and update existing provider
                for i, p in enumerate(self.config_data["api_providers"]):
                    if p["name"] == self.api_tree.item(item, "values")[0]:
                        self.config_data["api_providers"][i] = provider
                        break
            else:
                self.config_data["api_providers"].append(provider)
            
            self.update_ui()
            dialog.destroy()
        
        ttk.Button(btn_frame, text="保存", command=save_provider).pack(side=tk.LEFT, padx=5)
        ttk.Button(btn_frame, text="取消", command=dialog.destroy).pack(side=tk.LEFT, padx=5)
    
    def remove_api_provider(self):
        selected = self.api_tree.selection()
        if not selected:
            messagebox.showwarning("警告", "请选择要删除的提供商")
            return
        
        if messagebox.askyesno("确认", "确定要删除此提供商吗？"):
            provider_name = self.api_tree.item(selected[0], "values")[0]
            self.config_data["api_providers"] = [
                p for p in self.config_data["api_providers"] 
                if p["name"] != provider_name
            ]
            self.update_ui()
    
    def add_model(self):
        self.edit_model(is_new=True)
    
    def edit_model(self, item=None, is_new=False):
        # If called from edit button, get selected item
        if not is_new and item is None:
            selected = self.models_tree.selection()
            if not selected:
                messagebox.showwarning("警告", "请选择要编辑的模型")
                return
            item = selected[0]
            
        dialog = tk.Toplevel(self.root)
        dialog.title("编辑模型")
        dialog.transient(self.root)
        dialog.grab_set()
        
        # Form fields
        ttk.Label(dialog, text="名称:").grid(row=0, column=0, padx=5, pady=5, sticky=tk.E)
        name_entry = ttk.Entry(dialog)
        name_entry.grid(row=0, column=1, padx=5, pady=5, sticky=tk.W+tk.E)
        
        ttk.Label(dialog, text="标识符:").grid(row=1, column=0, padx=5, pady=5, sticky=tk.E)
        id_entry = ttk.Entry(dialog)
        id_entry.grid(row=1, column=1, padx=5, pady=5, sticky=tk.W+tk.E)
        
        ttk.Label(dialog, text="提供商:").grid(row=2, column=0, padx=5, pady=5, sticky=tk.E)
        provider_var = tk.StringVar()
        providers = [p["name"] for p in self.config_data.get("api_providers", [])]
        ttk.Combobox(dialog, textvariable=provider_var, values=providers).grid(row=2, column=1, padx=5, pady=5, sticky=tk.W+tk.E)
        
        ttk.Label(dialog, text="输入价格:").grid(row=3, column=0, padx=5, pady=5, sticky=tk.E)
        price_in_entry = ttk.Entry(dialog)
        price_in_entry.insert(0, "0")
        price_in_entry.grid(row=3, column=1, padx=5, pady=5, sticky=tk.W+tk.E)
        
        ttk.Label(dialog, text="输出价格:").grid(row=4, column=0, padx=5, pady=5, sticky=tk.E)
        price_out_entry = ttk.Entry(dialog)
        price_out_entry.insert(0, "0")
        price_out_entry.grid(row=4, column=1, padx=5, pady=5, sticky=tk.W+tk.E)

        ttk.Label(dialog, text="最大上下文Token:").grid(row=5, column=0, padx=5, pady=5, sticky=tk.E)
        max_context_entry = ttk.Entry(dialog)
        max_context_entry.insert(0, "32768")
        max_context_entry.grid(row=5, column=1, padx=5, pady=5, sticky=tk.W+tk.E)
        
        force_stream_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(dialog, text="强制流式模式", variable=force_stream_var).grid(row=6, column=0, columnspan=2, pady=5, sticky=tk.W)

        tool_call_compat_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(dialog, text="启用Tool Call兼容模式", variable=tool_call_compat_var).grid(row=7, column=0, columnspan=2, pady=5, sticky=tk.W)
        
        # Enhanced extra params section
        extra_params_frame = ttk.LabelFrame(dialog, text="额外参数 (TOML格式)", padding=5)
        extra_params_frame.grid(row=8, column=0, columnspan=2, padx=5, pady=5, sticky=tk.W+tk.E+tk.N+tk.S)
        
        # Text widget with syntax highlighting
        extra_params_text = tk.Text(extra_params_frame, height=6, width=50, wrap=tk.NONE)
        extra_params_text.pack(fill=tk.BOTH, expand=True)
        
        # Add scrollbars
        y_scroll = ttk.Scrollbar(extra_params_frame, orient=tk.VERTICAL, command=extra_params_text.yview)
        y_scroll.pack(side=tk.RIGHT, fill=tk.Y)
        extra_params_text.config(yscrollcommand=y_scroll.set)
        
        x_scroll = ttk.Scrollbar(extra_params_frame, orient=tk.HORIZONTAL, command=extra_params_text.xview)
        x_scroll.pack(side=tk.BOTTOM, fill=tk.X)
        extra_params_text.config(xscrollcommand=x_scroll.set)
        
        # Button frame
        btn_frame = ttk.Frame(extra_params_frame)
        btn_frame.pack(fill=tk.X, pady=(5,0))
        
        # Add buttons
        ttk.Button(btn_frame, text="格式化", 
                 command=lambda: self.format_toml(extra_params_text)).pack(side=tk.LEFT, padx=2)
        ttk.Button(btn_frame, text="清空", 
                 command=lambda: extra_params_text.delete("1.0", tk.END)).pack(side=tk.LEFT, padx=2)
        ttk.Button(btn_frame, text="示例", 
                 command=lambda: self.insert_example(extra_params_text)).pack(side=tk.LEFT, padx=2)
        
        # Add syntax highlighting tags
        extra_params_text.tag_config("key", foreground="blue")
        extra_params_text.tag_config("string", foreground="green")
        extra_params_text.tag_config("number", foreground="red")
        extra_params_text.tag_config("comment", foreground="gray")
        
        # Bind key events for basic syntax highlighting
        extra_params_text.bind("<KeyRelease>", lambda e: self.highlight_toml(extra_params_text))
        
        # Populate fields from config data if editing existing model
        if item and not is_new:
            model_name = self.models_tree.item(item, "values")[0]
            for model in self.config_data.get("models", []):
                if model["name"] == model_name:
                    name_entry.insert(0, model.get("name", ""))
                    id_entry.insert(0, model.get("model_identifier", ""))
                    provider_var.set(model.get("api_provider", ""))
                    price_in_entry.delete(0, tk.END)
                    price_in_entry.insert(0, str(model.get("price_in", 0)))
                    price_out_entry.delete(0, tk.END)
                    price_out_entry.insert(0, str(model.get("price_out", 0)))
                    max_context_entry.delete(0, tk.END)
                    max_context_entry.insert(0, str(model.get("max_context", 32768)))
                    force_stream_var.set(model.get("force_stream_mode", False))
                    tool_call_compat_var.set(model.get("tool_call_compat", False))
                    
                    # Populate extra params if they exist
                    if "extra_params" in model:
                        extra_params_text.insert(tk.END, tomli_w.dumps(model["extra_params"]))
                    break
        
        # Buttons
        btn_frame = ttk.Frame(dialog)
        btn_frame.grid(row=9, column=0, columnspan=2, pady=10)
        
        def save_model():
            try:
                max_context = int(max_context_entry.get())
            except ValueError:
                messagebox.showerror("错误", "max_context 必须是整数")
                return
            if max_context <= 0:
                messagebox.showerror("错误", "max_context 必须大于0")
                return

            existing_model = {}
            if item:
                old_name = self.models_tree.item(item, "values")[0]
                for m in self.config_data.get("models", []):
                    if m.get("name") == old_name:
                        existing_model = dict(m)
                        break

            model = dict(existing_model)
            model.update({
                "name": name_entry.get(),
                "model_identifier": id_entry.get(),
                "api_provider": provider_var.get(),
                "price_in": float(price_in_entry.get()),
                "price_out": float(price_out_entry.get()),
                "max_context": max_context,
                "tool_call_compat": bool(tool_call_compat_var.get()),
            })
            
            if force_stream_var.get():
                model["force_stream_mode"] = True
            elif "force_stream_mode" in model:
                model["force_stream_mode"] = False
                
            # Handle extra params
            extra_params = extra_params_text.get("1.0", tk.END).strip()
            if extra_params:
                try:
                    model["extra_params"] = tomli.loads(extra_params)
                except Exception as e:
                    messagebox.showerror("错误", f"解析额外参数失败: {str(e)}")
                    return
            elif "extra_params" in model:
                model.pop("extra_params", None)
            
            if "models" not in self.config_data:
                self.config_data["models"] = []
            
            if item:
                # Find and update existing model
                for i, m in enumerate(self.config_data["models"]):
                    if m["name"] == self.models_tree.item(item, "values")[0]:
                        self.config_data["models"][i] = model
                        break
            else:
                self.config_data["models"].append(model)
            
            self.update_ui()
            dialog.destroy()
        
        ttk.Button(btn_frame, text="保存", command=save_model).pack(side=tk.LEFT, padx=5)
        ttk.Button(btn_frame, text="取消", command=dialog.destroy).pack(side=tk.LEFT, padx=5)
    
    def format_toml(self, text_widget):
        """Format TOML content in the text widget"""
        content = text_widget.get("1.0", tk.END).strip()
        if not content:
            return
            
        try:
            parsed = tomli.loads(content)
            formatted = tomli_w.dumps(parsed)
            text_widget.delete("1.0", tk.END)
            text_widget.insert("1.0", formatted)
        except Exception as e:
            messagebox.showerror("TOML格式错误", f"无法格式化TOML内容: {str(e)}")

    def toggle_extra_params(self, text_widget, frame):
        """Toggle visibility of extra params text widget"""
        if text_widget.winfo_ismapped():
            text_widget.grid_remove()
            for child in frame.winfo_children():
                if isinstance(child, ttk.Button):
                    child.config(text="显示额外参数")
        else:
            text_widget.grid()
            for child in frame.winfo_children():
                if isinstance(child, ttk.Button):
                    child.config(text="隐藏额外参数")

    def highlight_toml(self, text_widget):
        """Apply basic syntax highlighting to TOML content"""
        text = text_widget.get("1.0", tk.END)
        
        # Clear all tags first
        for tag in ["key", "string", "number", "comment"]:
            text_widget.tag_remove(tag, "1.0", tk.END)
        
        # Simple regex patterns for highlighting
        import re
        patterns = [
            (r'#.*$', "comment"),  # Comments
            (r'(\w+)\s*=', "key"),  # Keys
            (r'".*?"', "string"),  # Double quoted strings
            (r"'.*?'", "string"),  # Single quoted strings
            (r'\b\d+\b', "number"),  # Integers
            (r'\b\d+\.\d+\b', "number")  # Floats
        ]
        
        for pattern, tag in patterns:
            for match in re.finditer(pattern, text, re.MULTILINE):
                start = f"1.0 + {match.start()}c"
                end = f"1.0 + {match.end()}c"
                text_widget.tag_add(tag, start, end)

    def insert_example(self, text_widget):
        """Insert example TOML for extra params"""
        example = """# 示例额外参数配置
enable_thinking = false  # 禁用思考
thinking_budget = 256  # 最大思考token
context_reserve_ratio = 0.1  # 上下文预留比例
context_reserve_tokens = 512  # 上下文固定预留token
"""
        text_widget.delete("1.0", tk.END)
        text_widget.insert("1.0", example)
        self.highlight_toml(text_widget)

    def remove_model(self):
        selected = self.models_tree.selection()
        if not selected:
            messagebox.showwarning("警告", "请选择要删除的模型")
            return
        
        if messagebox.askyesno("确认", "确定要删除此模型吗？"):
            model_name = self.models_tree.item(selected[0], "values")[0]
            self.config_data["models"] = [
                m for m in self.config_data["models"] 
                if m["name"] != model_name
            ]
            self.update_ui()
    
    def edit_task(self):
        selected = self.tasks_tree.selection()
        if not selected:
            messagebox.showwarning("警告", "请选择要编辑的任务")
            return
        
        task_name = self.tasks_tree.item(selected[0], "values")[0]
        
        # Handle both nested and flat model_tasks structures
        if "model_tasks" in self.config_data:
            task_config = self.config_data["model_tasks"].get(task_name, {})
        else:
            task_key = f"model_tasks.{task_name}"
            task_config = self.config_data.get(task_key, {})
        
        # Convert string model_list to array if needed
        if "model_list" in task_config and isinstance(task_config["model_list"], str):
            task_config["model_list"] = [m.strip() for m in task_config["model_list"].split(",")]
        
        dialog = tk.Toplevel(self.root)
        dialog.title(f"编辑任务: {task_name}")
        dialog.transient(self.root)
        dialog.grab_set()
        
        # Form fields
        # Model list with scrollable listbox and add/remove buttons
        ttk.Label(dialog, text="模型列表:").grid(row=0, column=0, padx=5, pady=5, sticky=tk.NE)
        
        model_control_frame = ttk.Frame(dialog)
        model_control_frame.grid(row=0, column=1, padx=5, pady=5, sticky=tk.W+tk.E+tk.N+tk.S)
        
        # Available models list
        ttk.Label(model_control_frame, text="可用模型:").pack(anchor=tk.W)
        available_frame = ttk.Frame(model_control_frame)
        available_frame.pack(fill=tk.BOTH, expand=True)
        
        available_listbox = tk.Listbox(available_frame, selectmode=tk.SINGLE, height=4)
        available_listbox.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        
        scrollbar1 = ttk.Scrollbar(available_frame, orient=tk.VERTICAL)
        scrollbar1.pack(side=tk.RIGHT, fill=tk.Y)
        available_listbox.config(yscrollcommand=scrollbar1.set)
        scrollbar1.config(command=available_listbox.yview)
        
        # Selected models list
        ttk.Label(model_control_frame, text="已选模型:").pack(anchor=tk.W)
        selected_frame = ttk.Frame(model_control_frame)
        selected_frame.pack(fill=tk.BOTH, expand=True)
        
        selected_listbox = tk.Listbox(selected_frame, selectmode=tk.SINGLE, height=4)
        selected_listbox.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        
        scrollbar2 = ttk.Scrollbar(selected_frame, orient=tk.VERTICAL)
        scrollbar2.pack(side=tk.RIGHT, fill=tk.Y)
        selected_listbox.config(yscrollcommand=scrollbar2.set)
        scrollbar2.config(command=selected_listbox.yview)
        
        # Add/remove buttons
        btn_frame = ttk.Frame(model_control_frame)
        btn_frame.pack(fill=tk.X, pady=5)
        
        def add_model():
            selection = available_listbox.curselection()
            if selection:
                model = available_listbox.get(selection[0])
                if model not in selected_listbox.get(0, tk.END):
                    selected_listbox.insert(tk.END, model)
        
        def remove_model():
            selection = selected_listbox.curselection()
            if selection:
                selected_listbox.delete(selection[0])
        
        ttk.Button(btn_frame, text="添加 →", command=add_model).pack(side=tk.LEFT, padx=2)
        ttk.Button(btn_frame, text="← 移除", command=remove_model).pack(side=tk.LEFT, padx=2)
        ttk.Button(btn_frame, text="↑ 上移", command=lambda: move_model(-1)).pack(side=tk.LEFT, padx=2)
        ttk.Button(btn_frame, text="↓ 下移", command=lambda: move_model(1)).pack(side=tk.LEFT, padx=2)
        
        def move_model(direction):
            """Move selected model up or down in the list"""
            selection = selected_listbox.curselection()
            if not selection:
                return
            index = selection[0]
            new_index = index + direction
            
            # Check bounds
            if new_index < 0 or new_index >= selected_listbox.size():
                return
                
            # Get the model name
            model = selected_listbox.get(index)
            
            # Remove and reinsert at new position
            selected_listbox.delete(index)
            selected_listbox.insert(new_index, model)
            selected_listbox.selection_set(new_index)
        
        # Populate available models
        if "models" in self.config_data:
            for model in self.config_data["models"]:
                available_listbox.insert(tk.END, model["name"])
        
        # Populate selected models
        for model_name in task_config.get("model_list", []):
            if isinstance(model_name, str):  # Handle both string and list formats
                selected_listbox.insert(tk.END, model_name)
        
        ttk.Label(dialog, text="温度(0-1):").grid(row=1, column=0, padx=5, pady=5, sticky=tk.E)
        temp_entry = ttk.Entry(dialog)
        temp_entry.insert(0, str(task_config.get("temperature", "")))
        temp_entry.grid(row=1, column=1, padx=5, pady=5, sticky=tk.W+tk.E)
        
        ttk.Label(dialog, text="最大Token数(1-8000):").grid(row=2, column=0, padx=5, pady=5, sticky=tk.E)
        tokens_entry = ttk.Entry(dialog)
        tokens_entry.insert(0, str(task_config.get("max_tokens", "")))
        tokens_entry.grid(row=2, column=1, padx=5, pady=5, sticky=tk.W+tk.E)
        
        # Handle extra params if they exist
        if "extra_params" in task_config:
            # Create text widget for extra params
            ttk.Label(dialog, text="额外参数:").grid(row=3, column=0, padx=5, pady=5, sticky=tk.E)
            extra_params_text = tk.Text(dialog, height=4, width=30)
            extra_params_text.insert(tk.END, str(task_config["extra_params"]))
            extra_params_text.grid(row=3, column=1, padx=5, pady=5, sticky=tk.W+tk.E)
        
        # Buttons
        btn_frame = ttk.Frame(dialog)
        btn_frame.grid(row=5, column=0, columnspan=2, pady=10)
        
        def save_task():
            try:
                # Get temperature value
                temp = float(temp_entry.get()) if temp_entry.get() else None
                
                # Validate max tokens
                tokens = int(tokens_entry.get()) if tokens_entry.get() else None
                if tokens is not None and (tokens < 1 or tokens > 8000):
                    raise ValueError("最大Token数必须在1-8000之间")
                
                # Get selected models from both listboxes
                model_list = list(selected_listbox.get(0, tk.END))
                if not model_list:
                    raise ValueError("至少需要选择一个模型")
                
                task_config["model_list"] = model_list
                
                if temp is not None:
                    task_config["temperature"] = temp
                if tokens is not None:
                    task_config["max_tokens"] = tokens
                
                # Handle extra params if they exist in the dialog
                if 'extra_params_text' in locals():
                    extra_params = extra_params_text.get("1.0", tk.END).strip()
                    if extra_params:
                        try:
                            task_config["extra_params"] = tomli.loads(extra_params)
                        except Exception as e:
                            messagebox.showerror("错误", f"解析额外参数失败: {str(e)}")
                            return
                
                # Ensure model_tasks exists
                if "model_tasks" not in self.config_data:
                    self.config_data["model_tasks"] = {}
                
                # Update task config
                self.config_data["model_tasks"][task_name] = task_config
                
                self.update_ui()
                dialog.destroy()
            except ValueError as e:
                messagebox.showerror("输入错误", str(e))
        
        ttk.Button(btn_frame, text="保存", command=save_task).pack(side=tk.LEFT, padx=5)
        ttk.Button(btn_frame, text="取消", command=dialog.destroy).pack(side=tk.LEFT, padx=5)

if __name__ == "__main__":
    root = tk.Tk()
    app = ConfigManager(root)
    root.mainloop()
