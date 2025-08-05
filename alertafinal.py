import threading
import time
import sys
import winsound
import queue
import tkinter as tk
from tkinter import ttk, messagebox, scrolledtext
import uuid
import requests
import hashlib
import json
import os
import datetime
import platform

from iqoptionapi.stable_api import IQ_Option
from colorama import init, Fore, Style
from collections import defaultdict

# Initialize Colorama for console output
init(autoreset=True)

# --- GLOBAL CONFIGURATIONS ---
DEFAULT_IQ_USER = "seu-email@gmail.com" # CHANGE THIS
DEFAULT_IQ_PASS = "sua-senha-secreta" # CHANGE THIS - IMPORTANT: NEVER store passwords in plain text in production
DEFAULT_ACCOUNT_TYPE = "PRACTICE"

DEFAULT_TIMEFRAME = 60 # Timeframe em segundos (ex: 60 para 1 minuto)

DEFAULT_ATIVOS_PARA_ANALISE = [
    "ADAUSD", "ATOMUSD-OTC", "AUDCAD-OTC", "AUDCHF-OTC", "AUDNZD-OTC",
    "BCHUSD-OTC", "BONKUSD-OTC", "CADCHF-OTC", "CADJPY-OTC", "DOTUSD-OTC",
    "ETHUSD-OTC", "EURCHF-OTC", "EURGBP-OTC", "EURUSD-OTC", "FLOKIUSD-OTC",
    "GBPJPY-OTC", "GBPUSD-OTC", "GBPCAD-OTC", "GRTUSD-OTC", "HBARUSD-OTC",
    "IMXUSD-OTC", "INJUSD-OTC", "JUPUSD-OTC", "LINKUSD-OTC", "NZDCHF-OTC",
    "ONYXCOINUSD-OTC", "PEPEUSD-OTC", "PENGUUSD-OTC",
    "PYTHUSD-OTC", "RONINUSD-OTC", "SANDUSD-OTC", "SEIUSD-OTC", "STXUSD-OTC",
    "SUIUSD-OTC", "TIAUSD-OTC", "TRON-OTC", "USDCAD-OTC", "USDCHF-OTC",
    "USDJPY-OTC", "XAUUSD-OTC", "XNGUSD-OTC"
]

# Parâmetros da Estratégia EMA (Fixa)
DEFAULT_EMA_CURTA = 3
DEFAULT_EMA_LONGA = 33
DEFAULT_QTD_VELAS_ANALISE = 400
DEFAULT_MAX_VELAS_REVERSAO = 5

# --- CONFIGURAÇÃO DO SISTEMA DE LICENÇA ---
LICENSE_SERVER_URL = "https://server-licenca-app.onrender.com/api/v1/activate"
LICENSE_SERVER_VALIDATE_URL = "https://server-licenca-app.onrender.com/api/v1/licenses"
LICENSE_FILE = 'license.dat'
VALIDATION_PERIOD_MINUTES = 1 # Para teste, mude para 60 * 24 para checar a cada 24h em produção.

# --- END GLOBAL CONFIGURATIONS ---

# Global queues for communication
command_queue = queue.Queue()
update_queue = queue.Queue()

# --- BOT STATE & CONFIGURATION CLASS ---
class BotConfig:
    def __init__(self):
        self.user = DEFAULT_IQ_USER
        self.password = DEFAULT_IQ_PASS
        self.account_type = DEFAULT_ACCOUNT_TYPE
        self.timeframe = DEFAULT_TIMEFRAME
        self.ativos_para_analise = DEFAULT_ATIVOS_PARA_ANALISE
        self.ema_curta = DEFAULT_EMA_CURTA
        self.ema_longa = DEFAULT_EMA_LONGA
        self.qtd_velas_analise = DEFAULT_QTD_VELAS_ANALISE
        self.max_velas_reversao = DEFAULT_MAX_VELAS_REVERSAO
        self.last_cross_time = defaultdict(float)
        self.historico_reversoes_cruzamento = []
        self.max_historico_reversoes = 5
        self.monitorando_reversao = {}
        # NOVO: Cache de análise histórica de reversões
        self.historico_reversao_cache = defaultdict(list)
        self.media_reversao_cache = defaultdict(int)

bot_config = BotConfig()

# Global variables for bot state
DEBUG_MODE = True
parar_bot_event = threading.Event()
ativos_sem_velas = defaultdict(int)
ativos_sem_velas_lock = threading.Lock()

# --- FUNÇÕES DE VALIDAÇÃO E GERENCIAMENTO DE LICENÇA ---
def get_device_id():
    try:
        system_info = f"{platform.node()}-{platform.processor()}-{platform.system()}-{platform.machine()}"
        device_id = hashlib.sha256(system_info.encode()).hexdigest()
        return device_id
    except Exception as e:
        log(f"Erro ao gerar ID do dispositivo: {e}", "ERROR")
        return None

class LicenseManager:
    def __init__(self, filename=LICENSE_FILE):
        self.filename = filename
        self.license_key = None
        self.is_active = False
        self.last_validation = None
        self.load_license()

    def load_license(self):
        try:
            with open(self.filename, 'r') as f:
                data = json.load(f)
                self.license_key = data.get("key")
                self.is_active = data.get("active", False)
                last_val_str = data.get("last_validation")
                if last_val_str:
                    self.last_validation = datetime.datetime.fromisoformat(last_val_str)
                return True
        except (FileNotFoundError, json.JSONDecodeError):
            self.license_key = None
            self.is_active = False
            return False

    def save_license(self, key):
        try:
            self.last_validation = datetime.datetime.now()
            data = {
                "key": key, 
                "active": True, 
                "last_validation": self.last_validation.isoformat()
            }
            with open(self.filename, 'w') as f:
                json.dump(data, f)
            self.license_key = key
            self.is_active = True
        except Exception as e:
            log(f"Falha ao salvar o arquivo de licença: {e}", "ERROR")

    def delete_license_file(self):
        if os.path.exists(self.filename):
            os.remove(self.filename)
            self.license_key = None
            self.is_active = False
            self.last_validation = None

def validate_license_on_server(license_key, device_id):
    try:
        response = requests.get(LICENSE_SERVER_VALIDATE_URL, timeout=15)
        response.raise_for_status()
        licenses = response.json()
        
        for lic in licenses:
            if lic['key'] == license_key:
                if lic['revoked'] or lic['status'] != 'active' or lic['device_id'] != device_id:
                    return False, "Licença inválida, revogada ou ativa em outro dispositivo."
                return True, "Licença validada com sucesso."
        
        return False, "Chave de licença não encontrada no servidor."
        
    except requests.exceptions.RequestException as e:
        log(f"Falha na comunicação com o servidor durante a validação: {e}", "ERROR")
        return True, "Falha na validação. Mantendo a chave local por enquanto."
    except Exception as e:
        log(f"Erro inesperado durante a validação da licença: {e}", "ERROR")
        return True, "Falha na validação. Mantendo a chave local por enquanto."

def validate_license_periodically():
    license_manager = LicenseManager()
    if not license_manager.is_active:
        return True

    minutes_since_last_validation = (datetime.datetime.now() - license_manager.last_validation).total_seconds() / 60
    
    if minutes_since_last_validation < VALIDATION_PERIOD_MINUTES:
        log(f"Validação periódica não necessária. Última validação há {minutes_since_last_validation:.2f} minutos.", "INFO")
        return True

    log("Tentando revalidar a licença com o servidor...", "INFO")
    device_id = get_device_id()
    if not device_id:
        return False

    is_valid, message = validate_license_on_server(license_manager.license_key, device_id)
    if is_valid:
        license_manager.save_license(license_manager.license_key) # Atualiza a data de validacao
        return True
    else:
        log(f"Validação periódica falhou: {message}", "ERROR")
        license_manager.delete_license_file()
        messagebox.showerror("Licença Inválida", "Sua licença não é mais válida. O programa será encerrado.")
        os._exit(1)
        return False

def activate_license_on_server(license_key):
    device_id = get_device_id()
    if not device_id:
        return False
        
    log(f"Tentando ativar a licença... Chave: {license_key}", "INFO")

    try:
        payload = {
            "license_key": license_key,
            "device_id": device_id
        }
        
        response = requests.post(LICENSE_SERVER_URL, json=payload, timeout=15)
        
        try:
            result = response.json()
        except json.JSONDecodeError:
            log(f"Erro ao decodificar a resposta do servidor. Resposta: {response.text}", "ERROR")
            return False

        if response.status_code == 200:
            if result.get("success"):
                return True
            else:
                log(f"Falha na ativação: {result.get('message', 'Erro desconhecido')}", "ERROR")
                return False
        else:
            log(f"Falha na comunicação com o servidor. Status: {response.status_code}, Resposta: {result.get('message', 'N/A')}", "ERROR")
            return False
            
    except requests.exceptions.RequestException as e:
        log(f"Erro de conexão com o servidor de licenças: {e}", "ERROR")
        return False

# --- FUNÇÕES PRINCIPAIS DO BOT ---

def tocar_som(tipo):
    try:
        if tipo == 'crossover': winsound.Beep(1500, 250)
    except Exception: pass

def calcular_ema(prices, period):
    if not prices or period <= 0 or len(prices) < period:
        return None
    k = 2 / (period + 1)
    ema = [sum(prices[:period]) / period] 
    for price in prices[period:]: 
        ema.append((price * k) + (ema[-1] * (1 - k)))
    return ema

def check_ema_crossover_signal(velas):
    if len(velas) < bot_config.ema_longa + 1:
        return None
    closes = [v['close'] for v in velas]
    ema_curta_valores = calcular_ema(closes, bot_config.ema_curta)
    ema_longa_valores = calcular_ema(closes, bot_config.ema_longa)
    if not all([ema_curta_valores, ema_longa_valores]) or len(ema_curta_valores) < 2 or len(ema_longa_valores) < 2:
        return None
    ema_curta_atual = ema_curta_valores[-1]
    ema_longa_atual = ema_longa_valores[-1]
    ema_curta_anterior = ema_curta_valores[-2]
    ema_longa_anterior = ema_longa_valores[-2]
    signal = None
    if ema_curta_anterior <= ema_longa_anterior and ema_curta_atual > ema_longa_atual:
        signal = 'call'
    elif ema_curta_anterior >= ema_longa_anterior and ema_curta_atual < ema_longa_atual:
        signal = 'put'
    return signal

# NOVO: Função para analisar o histórico de reversões
def analyze_historical_reversals(api):
    log("Iniciando análise histórica de reversões...", "INFO")
    for ativo in bot_config.ativos_para_analise:
        try:
            velas_api = api.get_candles(ativo, bot_config.timeframe, 1000, time.time())
            if not velas_api or len(velas_api) < bot_config.ema_longa + bot_config.max_velas_reversao + 1:
                continue

            reversal_counts = []
            closes = [v['close'] for v in velas_api]
            ema_curta_valores = calcular_ema(closes, bot_config.ema_curta)
            ema_longa_valores = calcular_ema(closes, bot_config.ema_longa)

            if not ema_curta_valores or not ema_longa_valores:
                continue

            for i in range(bot_config.ema_longa + 1, len(velas_api) - bot_config.max_velas_reversao):
                ema_curta_ant = ema_curta_valores[i-1]
                ema_longa_ant = ema_longa_valores[i-1]
                ema_curta_atual = ema_curta_valores[i]
                ema_longa_atual = ema_longa_valores[i]

                signal = None
                if ema_curta_ant <= ema_longa_ant and ema_curta_atual > ema_longa_atual:
                    signal = 'call'
                elif ema_curta_ant >= ema_longa_ant and ema_curta_atual < ema_longa_atual:
                    signal = 'put'

                if signal:
                    for j in range(i + 1, i + 1 + bot_config.max_velas_reversao):
                        if j >= len(velas_api):
                            break
                        candle_direction = 'call' if velas_api[j]['close'] > velas_api[j]['open'] else 'put'
                        if (signal == 'call' and candle_direction == 'put') or (signal == 'put' and candle_direction == 'call'):
                            reversal_counts.append(j - i)
                            break
            
            if reversal_counts:
                media_reversao = round(sum(reversal_counts) / len(reversal_counts))
                bot_config.media_reversao_cache[ativo] = media_reversao
                log(f"[{ativo}] Média de reversão calculada: {media_reversao} vela(s) após o cruzamento.", "INFO")
            else:
                bot_config.media_reversao_cache[ativo] = 0

        except Exception as e:
            log(f"Erro ao analisar histórico para {ativo}: {e}", "ERROR")

    log("Análise histórica concluída.", "SUCCESS")
    update_queue.put(("historical_analysis_done", None))

def adicionar_reversao_ao_historico(ativo, direcao_cruzamento, timestamp_cruzamento, timestamp_reversao, velas_para_reverter):
    global bot_config
    reversao_data = {
        "timestamp_cruzamento": timestamp_cruzamento,
        "ativo": ativo,
        "direcao_cruzamento": direcao_cruzamento,
        "timestamp_reversao": timestamp_reversao, 
        "velas_para_reverter": velas_para_reverter
    }
    bot_config.historico_reversoes_cruzamento.insert(0, reversao_data)  
    if len(bot_config.historico_reversoes_cruzamento) > bot_config.max_historico_reversoes:
        bot_config.historico_reversoes_cruzamento.pop() 
    log(f"REVERSÃO REGISTRADA: [{ativo}] Cruzamento {direcao_cruzamento.upper()} em {time.strftime('%H:%M:%S', time.localtime(timestamp_cruzamento))} -> Reverteu em {velas_para_reverter} vela(s)", "SUCCESS")
    update_queue.put(("new_reversal_record", reversao_data))

def ciclo_principal_alerta_simples(api):
    global ativos_sem_velas
    ultimo_ping_tempo = time.time()
    
    log("Thread principal de alertas (cruzamentos EMA) iniciada.", "INFO")
    update_queue.put(("bot_status", "Rodando (Cruzamentos EMA)"))

    while not parar_bot_event.is_set():
        try:
            command = command_queue.get_nowait()
            if command[0] == "stop_bot":
                log("Comando de parada recebido da GUI.", "INFO")
                parar_bot_event.set()
                break
            elif command[0] == "update_config":
                new_config = command[1]
                log(f"Atualizando configuração do bot: {new_config}", "INFO")
                for key, value in new_config.items():
                    setattr(bot_config, key, value)
                log("Configuração do bot de alertas atualizada com sucesso.", "SUCCESS")
                bot_config.last_cross_time = defaultdict(float)
                bot_config.monitorando_reversao = {}
            elif command[0] == "change_account":
                new_account_type = command[1]
                if api.change_balance(new_account_type):
                    log(f"Conta alterada para {new_account_type} com sucesso!", "SUCCESS")
                    bot_config.account_type = new_account_type
                    update_queue.put(("account_type_changed", new_account_type))
                else:
                    log(f"Falha ao alterar conta para {new_account_type}.", "ERROR")
        except queue.Empty:
            pass

        try:
            current_time = time.time()
            if current_time - ultimo_ping_tempo >= 10:
                log(f"Bot de alertas ativo... Checando cruzamentos e reversões.", "INFO")
                ultimo_ping_tempo = current_time
            for ativo in bot_config.ativos_para_analise:
                velas_api = api.get_candles(ativo, bot_config.timeframe, bot_config.qtd_velas_analise, current_time)
                min_candles_required_for_ema = bot_config.ema_longa + 1
                if not velas_api or len(velas_api) < min_candles_required_for_ema:
                    log(f"[{ativo}] Não foi possível obter velas suficientes ({min_candles_required_for_ema} mínimas) para checar cruzamento. Obtidas: {len(velas_api) if velas_api else 0}.", "WARNING")
                    with ativos_sem_velas_lock:
                        ativos_sem_velas[ativo] += 1
                    continue
                current_candle_start_time = velas_api[-1]['from']
                sinal_cruzamento = check_ema_crossover_signal(velas_api)
                if sinal_cruzamento:
                    if bot_config.last_cross_time[ativo] != current_candle_start_time:
                        media_reversao = bot_config.media_reversao_cache.get(ativo, 0)
                        alerta_message = f"[{ativo}] CRUZAMENTO {sinal_cruzamento.upper()}! Reverte em ~{media_reversao} vela(s)."
                        log(alerta_message, "ALERT")
                        tocar_som('crossover')
                        bot_config.last_cross_time[ativo] = current_candle_start_time
                        bot_config.monitorando_reversao[ativo] = {
                            "timestamp_cruzamento": current_candle_start_time,
                            "direcao_cruzamento": sinal_cruzamento
                        }
                        update_queue.put(("new_alert", {
                            "ativo": ativo,
                            "direcao": sinal_cruzamento.upper(),
                            "estrategia": f"EMA {bot_config.ema_curta}/{bot_config.ema_longa} Cruzamento",
                            "hora": time.strftime('%H:%M:%S', time.localtime(current_time)),
                            "reversao_predita": f"~{media_reversao} vela(s)"
                        }))
                if ativo in bot_config.monitorando_reversao:
                    monitor_info = bot_config.monitorando_reversao[ativo]
                    timestamp_cruzamento = monitor_info["timestamp_cruzamento"]
                    direcao_cruzamento = monitor_info["direcao_cruzamento"]
                    idx_cross_candle = -1
                    for k, candle in enumerate(velas_api):
                        if candle['from'] == timestamp_cruzamento:
                            idx_cross_candle = k
                            break
                    if idx_cross_candle == -1:
                        log(f"[{ativo}] Vela de cruzamento ({time.strftime('%H:%M:%S', time.localtime(timestamp_cruzamento))}) não encontrada no histórico recente. Parando monitoramento de reversão.", "INFO")
                        del bot_config.monitorando_reversao[ativo]
                        continue
                    reversal_found = False
                    for j in range(idx_cross_candle + 1, len(velas_api)):
                        if j == len(velas_api) - 1:  
                            break
                        candle_to_check = velas_api[j]
                        velas_counter = j - (idx_cross_candle + 1)
                        candle_direction = None
                        if candle_to_check['close'] > candle_to_check['open']:
                            candle_direction = 'call'
                        elif candle_to_check['close'] < candle_to_check['open']:
                            candle_direction = 'put'
                        if (direcao_cruzamento == 'call' and candle_direction == 'put') or \
                           (direcao_cruzamento == 'put' and candle_direction == 'call'):
                            adicionar_reversao_ao_historico(
                                ativo, direcao_cruzamento, 
                                timestamp_cruzamento, 
                                candle_to_check['from'], 
                                velas_counter 
                            )
                            del bot_config.monitorando_reversao[ativo] 
                            reversal_found = True
                            break
                    velas_passadas_completas = len(velas_api) - 1 - (idx_cross_candle + 1)
                    if not reversal_found and velas_passadas_completas >= bot_config.max_velas_reversao:
                        log(f"[{ativo}] Cruzamento {direcao_cruzamento.upper()} em {time.strftime('%H:%M:%S', time.localtime(timestamp_cruzamento))} sem reversão detectada após {bot_config.max_velas_reversao} velas fechadas. Parando monitoramento.", "WARNING")
                        adicionar_reversao_ao_historico(
                            ativo, direcao_cruzamento, 
                            timestamp_cruzamento, 
                            None, 
                            f"> {bot_config.max_velas_reversao}"
                        )
                        del bot_config.monitorando_reversao[ativo]

            time.sleep(0.5)

        except Exception as e:
            log(f"Exceção CRÍTICA no ciclo principal de alerta: {e}", "ERROR")
            import traceback
            log(traceback.format_exc(), "ERROR")
            time.sleep(5)

    log("Bot de alertas (cruzamentos EMA) finalizado (thread principal).", "INFO")
    update_queue.put(("bot_status", "Parado"))

def iniciar_bot_thread_alerta_simples():
    if not validate_license_periodically():
        log("Não foi possível validar a licença. Bot não será iniciado.", "ERROR")
        update_queue.put(("bot_status", "Licença Inválida"))
        return

    log("Tentando iniciar a thread do bot de alertas...", "INFO")
    api = IQ_Option(bot_config.user, bot_config.password)
    log("Conectando à IQ Option...", "INFO")
    check, reason = api.connect()

    if not check:
        log(f"Falha na conexão: {reason}. Verifique suas credenciais ou conexão.", "ERROR")
        update_queue.put(("connection_status", "Falha na conexão"))
        return

    log("Conectado com sucesso!", "SUCCESS")
    update_queue.put(("connection_status", "Conectado"))
    
    # Inicia a análise histórica de reversões
    threading.Thread(target=analyze_historical_reversals, args=(api,), daemon=True).start()

    if api.change_balance(bot_config.account_type):
        log(f"Conta alterada para {bot_config.account_type}.", "INFO")
        update_queue.put(("account_type_changed", bot_config.account_type))
    else:
        log(f"ATENÇÃO: Não foi possível definir a conta para {bot_config.account_type}. Verifique.", "WARNING")
        
    try:
        alerta_thread = threading.Thread(target=ciclo_principal_alerta_simples, args=(api,))
        alerta_thread.daemon = True
        alerta_thread.start()
    except Exception as e:
        log(f"Erro ao iniciar a thread de alerta: {e}", "ERROR")
        update_queue.put(("bot_status", "Erro ao iniciar"))

# --- TKINTER GUI CLASS ---
class TradingBotGUI:
    def __init__(self, master):
        self.master = master
        master.title("IQ Option Bot de Alertas de Cruzamento EMA 3/33")
        master.geometry("1200x800")
        self.style = ttk.Style()
        self.style.theme_use("clam")
        self.main_frame = ttk.Frame(master)
        self.main_frame.pack(fill="both", expand=True, padx=10, pady=10)
        self.left_column_frame = ttk.Frame(self.main_frame)
        self.left_column_frame.pack(side="left", fill="both", padx=5, pady=5, expand=True)
        self.right_column_frame = ttk.Frame(self.main_frame)
        self.right_column_frame.pack(side="right", fill="both", padx=5, pady=5, expand=True)
        self.license_manager = LicenseManager()
        self.create_login_section(self.left_column_frame)
        self.create_settings_section(self.left_column_frame)
        self.create_status_section(self.right_column_frame)
        self.create_alert_log_section(self.right_column_frame)
        self.create_reversal_history_section(self.right_column_frame)
        self.create_logs_section(self.right_column_frame)
        for child in self.left_column_frame.winfo_children():
            child.pack_configure(pady=5)
        for child in self.right_column_frame.winfo_children():
            child.pack_configure(pady=5)
        self.master.protocol("WM_DELETE_WINDOW", self.on_closing)
        self.master.after(100, self.process_queue_updates)
        self.bot_running = False
        self.alert_history_count = 0
        self.reversal_history_count = 0

    def create_login_section(self, parent_frame):
        login_frame = ttk.LabelFrame(parent_frame, text="Controle do Bot")
        login_frame.pack(padx=10, pady=10, fill="x")
        license_frame = ttk.LabelFrame(login_frame, text="Ativação de Licença")
        license_frame.grid(row=0, column=0, columnspan=2, padx=5, pady=5, sticky="ew")
        ttk.Label(license_frame, text="Chave de Licença:").grid(row=0, column=0, padx=5, pady=2, sticky="w")
        self.license_key_entry = ttk.Entry(license_frame, width=30)
        self.license_key_entry.grid(row=0, column=1, padx=5, pady=2, sticky="ew")
        self.activate_button = ttk.Button(license_frame, text="Ativar Licença", command=self.handle_activation)
        self.activate_button.grid(row=1, column=0, columnspan=2, pady=5)
        self.license_status_label = ttk.Label(license_frame, text="Status: Aguardando...", foreground="orange")
        self.license_status_label.grid(row=2, column=0, columnspan=2, pady=5)
        ttk.Label(login_frame, text="Usuário:").grid(row=1, column=0, padx=5, pady=5, sticky="w")
        self.user_entry = ttk.Entry(login_frame, width=30)
        self.user_entry.insert(0, bot_config.user)
        self.user_entry.grid(row=1, column=1, padx=5, pady=5, sticky="ew")
        ttk.Label(login_frame, text="Senha:").grid(row=2, column=0, padx=5, pady=5, sticky="w")
        self.pass_entry = ttk.Entry(login_frame, width=30, show="*")
        self.pass_entry.insert(0, bot_config.password)
        self.pass_entry.grid(row=2, column=1, padx=5, pady=5, sticky="ew")
        self.connect_button = ttk.Button(login_frame, text="Conectar e Iniciar Alertas", command=self.start_bot_alert, state=tk.DISABLED)
        self.connect_button.grid(row=3, column=0, columnspan=2, pady=5)
        self.disconnect_button = ttk.Button(login_frame, text="Parar Alertas", command=self.stop_bot_alert, state=tk.DISABLED)
        self.disconnect_button.grid(row=4, column=0, columnspan=2, pady=5)
        self.connection_status_label = ttk.Label(login_frame, text="Status Conexão: Desconectado", foreground="red")
        self.connection_status_label.grid(row=5, column=0, columnspan=2, pady=5)
        account_type_frame = ttk.Frame(login_frame)
        account_type_frame.grid(row=6, column=0, columnspan=2, pady=5)
        ttk.Label(account_type_frame, text="Tipo de Conta:").pack(side="left", padx=5)
        self.account_type_var = tk.StringVar(value=bot_config.account_type)
        self.real_radio = ttk.Radiobutton(account_type_frame, text="Real", variable=self.account_type_var, value="REAL", command=self.change_account_type, state=tk.DISABLED)
        self.real_radio.pack(side="left", padx=5)
        self.practice_radio = ttk.Radiobutton(account_type_frame, text="Prática", variable=self.account_type_var, value="PRACTICE", command=self.change_account_type, state=tk.DISABLED)
        self.practice_radio.pack(side="left", padx=5)
        self.check_license_status()

    def check_license_status(self):
        if self.license_manager.load_license() and self.license_manager.is_active:
            self.license_status_label.config(text="Status: Licença Ativa!", foreground="green")
            self.connect_button.config(state=tk.NORMAL)
            self.activate_button.config(state=tk.DISABLED)
            self.license_key_entry.config(state=tk.DISABLED)
        else:
            self.license_status_label.config(text="Status: Licença Inativa. Por favor, ative.", foreground="red")
            self.connect_button.config(state=tk.DISABLED)
            self.activate_button.config(state=tk.NORMAL)
            self.license_key_entry.config(state=tk.NORMAL)
            self.license_key_entry.delete(0, tk.END)

    def handle_activation(self):
        license_key = self.license_key_entry.get().strip()
        if not license_key:
            messagebox.showerror("Erro", "Por favor, insira uma chave de licença.")
            return
        activation_thread = threading.Thread(target=self._run_activation, args=(license_key,), daemon=True)
        activation_thread.start()

    def _run_activation(self, license_key):
        self.activate_button.config(state=tk.DISABLED, text="Ativando...")
        self.license_key_entry.config(state=tk.DISABLED)
        self.license_status_label.config(text="Status: Ativando, aguarde...", foreground="blue")
        self.master.update_idletasks()
        if activate_license_on_server(license_key):
            self.license_manager.save_license(license_key)
            self.check_license_status()
            messagebox.showinfo("Sucesso", "Licença ativada com sucesso! Você já pode iniciar o bot.")
        else:
            self.check_license_status()
            messagebox.showerror("Falha", "Chave de licença inválida ou já em uso.")

    def create_settings_section(self, parent_frame):
        settings_frame = ttk.LabelFrame(parent_frame, text="Configurações de EMA e Ativos")
        settings_frame.pack(padx=10, pady=10, fill="x")
        row_idx = 0
        ttk.Label(settings_frame, text="Timeframe (segundos):").grid(row=row_idx, column=0, padx=5, pady=2, sticky="w")
        self.timeframe_var = tk.IntVar(value=bot_config.timeframe)
        ttk.Entry(settings_frame, textvariable=self.timeframe_var, width=10).grid(row=row_idx, column=1, padx=5, pady=2, sticky="ew")
        row_idx += 1
        ttk.Label(settings_frame, text="EMA Curta:").grid(row=row_idx, column=0, padx=5, pady=2, sticky="w")
        self.ema_curta_var = tk.IntVar(value=bot_config.ema_curta)
        ttk.Entry(settings_frame, textvariable=self.ema_curta_var, width=10).grid(row=row_idx, column=1, padx=5, pady=2, sticky="ew")
        row_idx += 1
        ttk.Label(settings_frame, text="EMA Longa:").grid(row=row_idx, column=0, padx=5, pady=2, sticky="w")
        self.ema_longa_var = tk.IntVar(value=bot_config.ema_longa)
        ttk.Entry(settings_frame, textvariable=self.ema_longa_var, width=10).grid(row=row_idx, column=1, padx=5, pady=2, sticky="ew")
        row_idx += 1
        ttk.Label(settings_frame, text="Quantidade de Velas (Análise):").grid(row=row_idx, column=0, padx=5, pady=2, sticky="w")
        self.qtd_velas_analise_var = tk.IntVar(value=bot_config.qtd_velas_analise)
        ttk.Entry(settings_frame, textvariable=self.qtd_velas_analise_var, width=10).grid(row=row_idx, column=1, padx=5, pady=2, sticky="ew")
        row_idx += 1
        ttk.Label(settings_frame, text="Máx. Velas para Reversão:").grid(row=row_idx, column=0, padx=5, pady=2, sticky="w")
        self.max_velas_reversao_var = tk.IntVar(value=bot_config.max_velas_reversao)
        ttk.Entry(settings_frame, textvariable=self.max_velas_reversao_var, width=10).grid(row=row_idx, column=1, padx=5, pady=2, sticky="ew")
        row_idx += 1
        ttk.Label(settings_frame, text="Ativos para Análise (separar por vírgula):").grid(row=row_idx, column=0, padx=5, pady=2, sticky="w")
        self.ativos_analise_var = tk.StringVar(value=", ".join(bot_config.ativos_para_analise))
        ttk.Entry(settings_frame, textvariable=self.ativos_analise_var, width=40).grid(row=row_idx, column=1, padx=5, pady=2, sticky="ew")
        row_idx += 1
        self.save_settings_button = ttk.Button(parent_frame, text="Aplicar Configurações", command=self.apply_settings)
        self.save_settings_button.pack(pady=10)

    def create_status_section(self, parent_frame):
        status_frame = ttk.LabelFrame(parent_frame, text="Status Geral do Bot")
        status_frame.pack(padx=10, pady=10, fill="x")
        self.bot_status_label = ttk.Label(status_frame, text="Bot: Parado", foreground="red")
        self.bot_status_label.grid(row=0, column=0, padx=5, pady=2, sticky="w")
        self.current_time_label = ttk.Label(status_frame, text="Hora Atual: N/A")
        self.current_time_label.grid(row=1, column=0, padx=5, pady=2, sticky="w")

    def create_alert_log_section(self, parent_frame):
        alert_log_frame = ttk.LabelFrame(parent_frame, text="Alertas de Cruzamento EMA (Tempo Real)")
        alert_log_frame.pack(padx=10, pady=10, fill="both", expand=True)
        self.alert_tree = ttk.Treeview(alert_log_frame, columns=("Hora", "Ativo", "Direção", "Estratégia", "Reversão Predita"), show="headings", height=10)
        self.alert_tree.heading("Hora", text="Hora")
        self.alert_tree.heading("Ativo", text="Ativo")
        self.alert_tree.heading("Direção", text="Direção")
        self.alert_tree.heading("Estratégia", text="Estratégia")
        self.alert_tree.heading("Reversão Predita", text="Reversão Predita")
        self.alert_tree.column("Hora", width=80, anchor="center")
        self.alert_tree.column("Ativo", width=80, anchor="center")
        self.alert_tree.column("Direção", width=80, anchor="center")
        self.alert_tree.column("Estratégia", width=180, anchor="center")
        self.alert_tree.column("Reversão Predita", width=100, anchor="center")
        self.alert_tree.pack(fill="both", expand=True, padx=5, pady=5)

    def create_reversal_history_section(self, parent_frame):
        reversal_history_frame = ttk.LabelFrame(parent_frame, text="Histórico de Reversões Pós-Cruzamento (Últimos 5)")
        reversal_history_frame.pack(padx=10, pady=10, fill="both", expand=True)
        self.reversal_tree = ttk.Treeview(reversal_history_frame, columns=("Ativo", "Cruzamento", "Velas p/ Reverter", "Hora Cruzamento", "Hora Reversão"), show="headings", height=5)
        self.reversal_tree.heading("Ativo", text="Ativo")
        self.reversal_tree.heading("Cruzamento", text="Cruzamento")
        self.reversal_tree.heading("Velas p/ Reverter", text="Velas p/ Reverter")
        self.reversal_tree.heading("Hora Cruzamento", text="Hora Cruzamento")
        self.reversal_tree.heading("Hora Reversão", text="Hora Reversão")
        self.reversal_tree.column("Ativo", width=70, anchor="center")
        self.reversal_tree.column("Cruzamento", width=90, anchor="center")
        self.reversal_tree.column("Velas p/ Reverter", width=120, anchor="center")
        self.reversal_tree.column("Hora Cruzamento", width=100, anchor="center")
        self.reversal_tree.column("Hora Reversão", width=100, anchor="center")
        self.reversal_tree.pack(fill="both", expand=True, padx=5, pady=5)

    def create_logs_section(self, parent_frame):
        logs_frame = ttk.LabelFrame(parent_frame, text="Logs Detalhados do Bot")
        logs_frame.pack(padx=10, pady=10, fill="both", expand=True)
        self.log_text = scrolledtext.ScrolledText(logs_frame, wrap=tk.WORD, height=8, width=80)
        self.log_text.pack(padx=5, pady=5, expand=True, fill="both")
        self.log_text.config(state=tk.DISABLED)

    def start_bot_alert(self):
        if self.bot_running:
            messagebox.showinfo("Bot Já Rodando", "O bot de alertas já está em execução.")
            return
        if not self.license_manager.is_active:
            messagebox.showerror("Licença Inválida", "Por favor, ative sua licença para iniciar o bot.")
            return
        user = self.user_entry.get()
        password = self.pass_entry.get()
        if not user or not password:
            messagebox.showerror("Erro de Login", "Por favor, insira seu usuário e senha da IQ Option.")
            return
        bot_config.user = user
        bot_config.password = password
        self.connect_button.config(state=tk.DISABLED, text="Iniciando Alertas...")
        self.disconnect_button.config(state=tk.DISABLED)
        self.connection_status_label.config(text="Status Conexão: Conectando...", foreground="orange")
        self.real_radio.config(state=tk.DISABLED)
        self.practice_radio.config(state=tk.DISABLED)
        thread = threading.Thread(target=iniciar_bot_thread_alerta_simples, daemon=True)
        thread.start()
        self.bot_running = True

    def stop_bot_alert(self):
        if not self.bot_running:
            messagebox.showinfo("Bot Parado", "O bot de alertas não está em execução.")
            return
        response = messagebox.askyesno("Parar Bot de Alertas", "Tem certeza que deseja parar o bot de alertas?")
        if response:
            command_queue.put(("stop_bot", None))

    def change_account_type(self):
        if self.bot_running:
            new_type = self.account_type_var.get()
            response = messagebox.askyesno("Mudar Tipo de Conta", f"Deseja mudar para a conta {new_type}? Isso pode causar uma reconexão.")
            if response:
                command_queue.put(("change_account", new_type))
            else:
                self.account_type_var.set(bot_config.account_type)
        else:
            bot_config.account_type = self.account_type_var.get()
            messagebox.showinfo("Tipo de Conta", f"Tipo de conta definido para {bot_config.account_type}. Inicie o bot para aplicar.")

    def apply_settings(self):
        try:
            new_config = {}
            new_config["timeframe"] = self.timeframe_var.get()
            new_config["ema_curta"] = self.ema_curta_var.get()
            new_config["ema_longa"] = self.ema_longa_var.get()
            new_config["qtd_velas_analise"] = self.qtd_velas_analise_var.get()
            new_config["max_velas_reversao"] = self.max_velas_reversao_var.get()
            new_config["ativos_para_analise"] = [a.strip().upper() for a in self.ativos_analise_var.get().split(',') if a.strip()]
            command_queue.put(("update_config", new_config))
            messagebox.showinfo("Configurações Aplicadas", "As novas configurações serão aplicadas no próximo ciclo de análise do bot.")
        except ValueError as e:
            messagebox.showerror("Erro de Configuração", f"Valores inválidos nos campos. Por favor, verifique: {e}")
        except Exception as e:
            messagebox.showerror("Erro Inesperado", f"Ocorreu um erro ao aplicar as configurações: {e}")

    def process_queue_updates(self):
        try:
            while True:
                update_type, data = update_queue.get_nowait()
                if update_type == "log":
                    self.update_log(data)
                elif update_type == "connection_status":
                    self.update_connection_status(data)
                elif update_type == "bot_status":
                    self.update_bot_status(data)
                elif update_type == "account_type_changed":
                    self.update_account_type_display(data)
                elif update_type == "new_alert":
                    self._add_new_alert_to_treeview(data)
                elif update_type == "new_reversal_record":
                    self._add_new_reversal_record_to_treeview(data)
                self.master.update_idletasks()
        except queue.Empty:
            pass
        finally:
            self.current_time_label.config(text=f"Hora Atual: {time.strftime('%H:%M:%S')}")
            self.master.after(1000, self.process_queue_updates)

    def update_log(self, message):
        self.log_text.config(state=tk.NORMAL)
        self.log_text.insert(tk.END, message + "\n")
        self.log_text.see(tk.END)
        self.log_text.config(state=tk.DISABLED)

    def update_connection_status(self, status):
        self.connection_status_label.config(text=f"Status Conexão: {status}")
        if status == "Conectado":
            self.connection_status_label.config(foreground="green")
            self.connect_button.config(state=tk.DISABLED)
            self.disconnect_button.config(state=tk.NORMAL)
            self.real_radio.config(state=tk.NORMAL)
            self.practice_radio.config(state=tk.NORMAL)
        elif status == "Falha na conexão":
            self.connection_status_label.config(foreground="red")
            self.connect_button.config(state=tk.NORMAL)
            self.disconnect_button.config(state=tk.DISABLED)
            self.real_radio.config(state=tk.DISABLED)
            self.practice_radio.config(state=tk.DISABLED)

    def update_bot_status(self, status):
        self.bot_status_label.config(text=f"Bot: {status}")
        if status == "Rodando (Cruzamentos EMA)":
            self.bot_status_label.config(foreground="green")
            self.connect_button.config(state=tk.DISABLED, text="Bot Rodando")
            self.disconnect_button.config(state=tk.NORMAL)
        elif status == "Parado" or status == "Erro ao iniciar":
            self.bot_status_label.config(foreground="red")
            self.connect_button.config(state=tk.NORMAL, text="Conectar e Iniciar Alertas")
            self.disconnect_button.config(state=tk.DISABLED)
            self.bot_running = False
            self.real_radio.config(state=tk.NORMAL)
            self.practice_radio.config(state=tk.NORMAL)
        elif status == "Licença Inválida":
            self.bot_status_label.config(foreground="red")
            self.connect_button.config(state=tk.DISABLED, text="Conectar e Iniciar Alertas")
            self.disconnect_button.config(state=tk.DISABLED)
            self.bot_running = False
            self.real_radio.config(state=tk.DISABLED)
            self.practice_radio.config(state=tk.DISABLED)
            self.check_license_status()

    def update_account_type_display(self, account_type):
        self.account_type_var.set(account_type)
        messagebox.showinfo("Conta Alterada", f"A conta foi alterada para {account_type} com sucesso!")

    def _add_new_alert_to_treeview(self, alert_data):
        self.alert_history_count += 1
        values = [
            alert_data['hora'],
            alert_data['ativo'],
            alert_data['direcao'],
            alert_data['estrategia'],
            alert_data.get('reversao_predita', 'N/A')
        ]
        self.alert_tree.insert("", 0, iid=f"alert_{self.alert_history_count}", values=values)
        max_alerts_to_show = 20
        if len(self.alert_tree.get_children()) > max_alerts_to_show:
            oldest_item = self.alert_tree.get_children()[-1]
            self.alert_tree.delete(oldest_item)

    def _add_new_reversal_record_to_treeview(self, record_data):
        self.reversal_history_count += 1
        while len(self.reversal_tree.get_children()) >= bot_config.max_historico_reversoes:
            oldest_item = self.reversal_tree.get_children()[-1]
            self.reversal_tree.delete(oldest_item)
        hora_cruzamento_str = time.strftime('%H:%M:%S', time.localtime(record_data['timestamp_cruzamento']))
        hora_reversao_str = time.strftime('%H:%M:%S', time.localtime(record_data['timestamp_reversao'])) if record_data['timestamp_reversao'] else "Não Reverteu"
        velas_para_reverter = record_data['velas_para_reverter']
        if isinstance(velas_para_reverter, int):
            velas_txt = f"{velas_para_reverter} vela(s)"
        elif isinstance(velas_para_reverter, str):
            velas_txt = velas_para_reverter
        else:
            velas_txt = "N/A"
        self.reversal_tree.insert("", 0, iid=f"reversal_{self.reversal_history_count}", values=(
            record_data['ativo'],
            record_data['direcao_cruzamento'].upper(),
            velas_txt,
            hora_cruzamento_str,
            hora_reversao_str
        ))

    def on_closing(self):
        if messagebox.askokcancel("Sair", "Deseja realmente fechar o bot? Isso irá parar todas as operações de alerta."):
            if self.bot_running:
                self.stop_bot_alert()
                time.sleep(1)
            self.master.destroy()
            sys.exit(0)

if __name__ == "__main__":
    root = tk.Tk()
    gui = TradingBotGUI(root)
    root.mainloop()