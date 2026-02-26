# -*- coding: utf-8 -*-
"""
Bot de trading dual para Ethereum (ETHUSDT) - Versión solo consola
Timeframes: 1m, 3m, 5m
Múltiples fuentes de datos: Binance (con fallback a data.binance.com), Bybit, Bitget
Optimización de períodos ADX, RSI, ATR (2,4,6,8,10,12,14,16)
Umbrales RSI, multiplicadores trailing stop y take profit, pendiente ADX
Backtesting con comisiones y deslizamiento
Asignación de capital según win rate long/short
Apalancamiento dinámico basado en win rate
Toda la salida por consola (sin base de datos)
"""

import sys
import requests
import pandas as pd
import numpy as np
import time
from datetime import datetime, timedelta
from itertools import product

# ==================== CONFIGURACIÓN ====================
SYMBOL = 'ETHUSDT'
INTERVAL_BASE = '1m'
HOURS = 24                      # Datos históricos para optimización (1 día)
LIMIT = 1000
REQUEST_TIMEOUT = 10            # Timeout para peticiones HTTP

# Parámetros de simulación realista
SLIPPAGE = 0.001                # 0.1% deslizamiento
COMMISSION = 0.001              # 0.1% comisión por operación
BASE_CAPITAL = 1000             # Capital base en USD (para simulación)
MAX_LEVERAGE = 100               # Apalancamiento máximo permitido
MIN_WIN_RATE_FOR_LEVERAGE = 0.4  # Mínimo win rate para usar apalancamiento

# Rangos de optimización
PERIOD_RANGE = [2, 4, 6, 8, 10, 12, 14, 16]
ADX_TH_RANGE = [20, 25, 30]          # Umbral de ADX para considerar tendencia
RSI_LOW_RANGE = [20, 25, 30, 35, 40]
RSI_HIGH_RANGE = [60, 65, 70, 75, 80]
MULT_STOP_RANGE = [1.0, 1.5, 2.0, 2.5, 3.0]   # Multiplicador para trailing stop
MULT_TP_RANGE = [1.0, 1.5, 2.0, 2.5, 3.0]     # Multiplicador para take profit
USE_SLOPE_OPTIONS = [False, True]              # Usar pendiente de ADX como filtro

# Timeframes a utilizar
TIMEFRAMES = {
    '1m': '1min',
    '3m': '3min',
    '5m': '5min'
}

# ==================== MÚLTIPLES FUENTES DE DATOS ====================
def fetch_klines_binance(symbol, interval, hours):
    """Intenta descargar de Binance (múltiples endpoints)"""
    endpoints = [
        "https://api.binance.com/api/v3/klines",
        "https://api1.binance.com/api/v3/klines",
        "https://api2.binance.com/api/v3/klines",
        "https://api3.binance.com/api/v3/klines",
        "https://data.binance.com/api/v3/klines"  # Endpoint alternativo para datos públicos
    ]
    
    end_time = int(time.time() * 1000)
    start_time = end_time - hours * 60 * 60 * 1000
    all_klines = []
    current_start = start_time
    
    for endpoint in endpoints:
        try:
            print(f"  Intentando con Binance ({endpoint.split('/')[2]})...")
            current_start = start_time
            all_klines = []
            
            while current_start < end_time:
                params = {
                    'symbol': symbol,
                    'interval': interval,
                    'startTime': current_start,
                    'limit': LIMIT
                }
                resp = requests.get(endpoint, params=params, timeout=REQUEST_TIMEOUT)
                resp.raise_for_status()
                data = resp.json()
                if not data:
                    break
                all_klines.extend(data)
                current_start = data[-1][0] + 1
            
            if all_klines:
                print(f"  ✅ Datos obtenidos de Binance ({endpoint.split('/')[2]})")
                return process_klines_data(all_klines)
        except Exception as e:
            print(f"  ❌ Error con {endpoint}: {str(e)[:50]}...")
            continue
    
    return None

def fetch_klines_bybit(symbol, interval, hours):
    """Descarga de Bybit (v5 API)"""
    try:
        print("  Intentando con Bybit...")
        # Mapeo de intervalos de Bybit
        interval_map = {
            '1m': '1',
            '3m': '3',
            '5m': '5',
            '15m': '15',
            '30m': '30',
            '1h': '60',
            '4h': '240',
            '1d': 'D'
        }
        bybit_interval = interval_map.get(interval, '1')
        
        end_time = int(time.time())
        start_time = end_time - hours * 60 * 60
        
        url = "https://api.bybit.com/v5/market/kline"
        params = {
            'category': 'spot',
            'symbol': symbol,
            'interval': bybit_interval,
            'start': start_time * 1000,
            'end': end_time * 1000,
            'limit': LIMIT
        }
        
        resp = requests.get(url, params=params, timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
        data = resp.json()
        
        if data['retCode'] == 0 and data['result']['list']:
            klines = data['result']['list']
            # Bybit devuelve en orden descendente, invertimos
            klines.reverse()
            
            # Convertir al formato estándar
            formatted = []
            for k in klines:
                formatted.append([
                    int(k[0]),           # timestamp
                    float(k[1]),          # open
                    float(k[2]),          # high
                    float(k[3]),          # low
                    float(k[4]),          # close
                    float(k[5]),          # volume
                    0, 0, 0, 0, 0, 0      # relleno para compatibilidad
                ])
            
            print("  ✅ Datos obtenidos de Bybit")
            return process_klines_data(formatted)
    except Exception as e:
        print(f"  ❌ Error con Bybit: {str(e)[:50]}...")
    
    return None

def fetch_klines_bitget(symbol, interval, hours):
    """Descarga de Bitget"""
    try:
        print("  Intentando con Bitget...")
        # Mapeo de intervalos de Bitget
        interval_map = {
            '1m': '1m',
            '3m': '3m',
            '5m': '5m',
            '15m': '15m',
            '30m': '30m',
            '1h': '1H',
            '4h': '4H',
            '1d': '1D'
        }
        bitget_interval = interval_map.get(interval, '1m')
        
        end_time = int(time.time() * 1000)
        start_time = end_time - hours * 60 * 60 * 1000
        
        # Bitget usa paginación con after/before
        url = "https://api.bitget.com/api/v2/spot/market/candles"
        params = {
            'symbol': symbol.replace('USDT', 'USDT_SPBL'),
            'granularity': bitget_interval,
            'startTime': start_time,
            'endTime': end_time,
            'limit': LIMIT
        }
        
        resp = requests.get(url, params=params, timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
        data = resp.json()
        
        if data['code'] == '00000' and data['data']:
            klines = data['data']
            
            # Convertir al formato estándar
            formatted = []
            for k in klines:
                formatted.append([
                    int(k[0]),           # timestamp
                    float(k[1]),          # open
                    float(k[2]),          # high
                    float(k[3]),          # low
                    float(k[4]),          # close
                    float(k[5]),          # volume
                    0, 0, 0, 0, 0, 0      # relleno para compatibilidad
                ])
            
            print("  ✅ Datos obtenidos de Bitget")
            return process_klines_data(formatted)
    except Exception as e:
        print(f"  ❌ Error con Bitget: {str(e)[:50]}...")
    
    return None

def process_klines_data(klines):
    """Convierte los datos crudos a DataFrame"""
    columns = [
        'timestamp', 'open', 'high', 'low', 'close', 'volume',
        'close_time', 'quote_asset_volume', 'number_of_trades',
        'taker_buy_base_asset_volume', 'taker_buy_quote_asset_volume', 'ignore'
    ]
    df = pd.DataFrame(klines, columns=columns)
    df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
    df.set_index('timestamp', inplace=True)
    for col in ['open', 'high', 'low', 'close']:
        df[col] = df[col].astype(float)
    return df[['open', 'high', 'low', 'close', 'volume']]

def fetch_klines_with_fallback(symbol, interval, hours):
    """Intenta múltiples exchanges hasta obtener datos"""
    print(f"\n📥 Descargando datos para {symbol} ({interval})...")
    
    # Lista de funciones de descarga en orden de preferencia
    fetch_functions = [
        fetch_klines_binance,
        fetch_klines_bybit,
        fetch_klines_bitget
    ]
    
    for fetch_func in fetch_functions:
        df = fetch_func(symbol, interval, hours)
        if df is not None and not df.empty:
            return df
        time.sleep(1)  # Pequeña pausa entre intentos
    
    print("❌ Todas las fuentes fallaron.")
    return None

# ==================== INDICADORES ====================
def compute_rsi(series, period):
    delta = series.diff()
    gain = (delta.where(delta > 0, 0)).rolling(window=period).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=period).mean()
    rs = gain / loss
    rsi = 100 - (100 / (1 + rs))
    return rsi

def compute_adx(df, period):
    high, low, close = df['high'], df['low'], df['close']
    tr1 = high - low
    tr2 = (high - close.shift()).abs()
    tr3 = (low - close.shift()).abs()
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    atr = tr.rolling(window=period).mean()
    up_move = high - high.shift()
    down_move = low.shift() - low
    plus_dm = np.where((up_move > down_move) & (up_move > 0), up_move, 0)
    minus_dm = np.where((down_move > up_move) & (down_move > 0), down_move, 0)
    plus_di = 100 * (pd.Series(plus_dm, index=df.index).rolling(window=period).mean() / atr)
    minus_di = 100 * (pd.Series(minus_dm, index=df.index).rolling(window=period).mean() / atr)
    dx = 100 * (abs(plus_di - minus_di) / (plus_di + minus_di))
    adx = dx.rolling(window=period).mean()
    adx_slope = adx.diff(3)  # pendiente de 3 velas
    df_out = pd.DataFrame(index=df.index)
    df_out['ADX'] = adx
    df_out['DI_plus'] = plus_di
    df_out['DI_minus'] = minus_di
    df_out['ADX_slope'] = adx_slope
    return df_out

def compute_atr(df, period):
    high, low, close = df['high'], df['low'], df['close']
    tr1 = high - low
    tr2 = (high - close.shift()).abs()
    tr3 = (low - close.shift()).abs()
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    atr = tr.rolling(window=period).mean()
    return atr

def resample_ohlc(df, rule):
    return df.resample(rule).agg({
        'open': 'first',
        'high': 'max',
        'low': 'min',
        'close': 'last',
        'volume': 'sum'
    }).dropna()

# ==================== BACKTEST POR DIRECCIÓN ====================
def backtest_direction(df, direction, params, capital=BASE_CAPITAL, leverage=1, verbose=False):
    """
    Backtest para una dirección específica (long o short).
    Retorna: trades, metrics
    """
    # Calcular indicadores con los períodos dados
    df = df.copy()
    df['RSI'] = compute_rsi(df['close'], params['rsi_period'])
    df['ATR'] = compute_atr(df, params['atr_period'])
    adx_df = compute_adx(df, params['adx_period'])
    df = df.join(adx_df[['ADX', 'DI_plus', 'DI_minus', 'ADX_slope']])

    # Variables de estado
    position = None
    entry_price = 0.0
    entry_atr = 0.0
    extreme_price = 0.0
    stop_price = 0.0
    take_profit = 0.0
    entry_time = None
    trades = []
    equity_curve = [0.0]

    for idx, row in df.iterrows():
        # Condiciones de entrada según dirección
        signal = False
        if direction == 'long':
            if pd.notna(row['ADX']) and pd.notna(row['DI_plus']) and pd.notna(row['DI_minus']) and pd.notna(row['RSI']):
                cond1 = row['ADX'] > params['adx_th']
                cond2 = row['DI_plus'] > row['DI_minus']
                cond3 = row['RSI'] < params['rsi_th']
                cond4 = (row['ADX_slope'] > 0) if params['use_slope'] else True
                signal = cond1 and cond2 and cond3 and cond4
        else:  # short
            if pd.notna(row['ADX']) and pd.notna(row['DI_plus']) and pd.notna(row['DI_minus']) and pd.notna(row['RSI']):
                cond1 = row['ADX'] > params['adx_th']
                cond2 = row['DI_minus'] > row['DI_plus']
                cond3 = row['RSI'] > params['rsi_th']
                cond4 = (row['ADX_slope'] < 0) if params['use_slope'] else True
                signal = cond1 and cond2 and cond3 and cond4

        # Gestión de posición
        if position is None:
            if signal:
                position = direction
                entry_price = row['close'] * (1 + SLIPPAGE) if direction == 'long' else row['close'] * (1 - SLIPPAGE)
                entry_atr = row['ATR']
                if direction == 'long':
                    extreme_price = row['high']
                    stop_price = extreme_price - params['mult_stop'] * entry_atr
                    take_profit = entry_price + params['mult_tp'] * entry_atr
                else:
                    extreme_price = row['low']
                    stop_price = extreme_price + params['mult_stop'] * entry_atr
                    take_profit = entry_price - params['mult_tp'] * entry_atr
                entry_time = idx
        else:
            exit_reason = None
            exit_price = None
            if direction == 'long':
                # Actualizar trailing stop
                if row['high'] > extreme_price:
                    extreme_price = row['high']
                    stop_price = extreme_price - params['mult_stop'] * entry_atr
                # Comprobar salidas
                if row['low'] <= stop_price:
                    exit_reason = 'trailing_stop'
                    exit_price = stop_price
                elif row['high'] >= take_profit:
                    exit_reason = 'take_profit'
                    exit_price = take_profit
            else:  # short
                if row['low'] < extreme_price:
                    extreme_price = row['low']
                    stop_price = extreme_price + params['mult_stop'] * entry_atr
                if row['high'] >= stop_price:
                    exit_reason = 'trailing_stop'
                    exit_price = stop_price
                elif row['low'] <= take_profit:
                    exit_reason = 'take_profit'
                    exit_price = take_profit

            if exit_reason:
                # Aplicar slippage a la salida
                if direction == 'long':
                    exit_price_adj = exit_price * (1 - SLIPPAGE)
                    ret = (exit_price_adj - entry_price) / entry_price - COMMISSION
                else:
                    exit_price_adj = exit_price * (1 + SLIPPAGE)
                    ret = (entry_price - exit_price_adj) / entry_price - COMMISSION

                trade = {
                    'tipo': direction,
                    'entrada': entry_price,
                    'salida': exit_price_adj,
                    'retorno': ret,
                    'razon': exit_reason
                }
                trades.append(trade)
                equity_curve.append(equity_curve[-1] + ret)
                position = None

    # Cerrar posición al final si está abierta
    if position is not None:
        last_row = df.iloc[-1]
        exit_price = last_row['close']
        if direction == 'long':
            exit_price_adj = exit_price * (1 - SLIPPAGE)
            ret = (exit_price_adj - entry_price) / entry_price - COMMISSION
        else:
            exit_price_adj = exit_price * (1 + SLIPPAGE)
            ret = (entry_price - exit_price_adj) / entry_price - COMMISSION
        trade = {
            'tipo': direction,
            'entrada': entry_price,
            'salida': exit_price_adj,
            'retorno': ret,
            'razon': 'fin_datos'
        }
        trades.append(trade)
        equity_curve.append(equity_curve[-1] + ret)

    # Métricas
    if trades:
        profits = [t['retorno'] for t in trades]
        total_profit = sum(profits)
        num_trades = len(trades)
        win_rate = sum(1 for p in profits if p > 0) / num_trades if num_trades > 0 else 0.0
        # Profit factor
        gross_profit = sum(p for p in profits if p > 0)
        gross_loss = abs(sum(p for p in profits if p < 0))
        profit_factor = gross_profit / gross_loss if gross_loss != 0 else float('inf')
        # Max drawdown
        equity = np.array(equity_curve)
        running_max = np.maximum.accumulate(equity)
        drawdown = (running_max - equity) / (1 + running_max) if running_max.size > 0 else np.array([0.0])
        max_dd = drawdown.max() if len(drawdown) > 0 else 0.0
    else:
        total_profit = 0.0
        num_trades = 0
        win_rate = 0.0
        profit_factor = 0.0
        max_dd = 0.0

    metrics = {
        'profit': total_profit,
        'trades': num_trades,
        'win_rate': win_rate,
        'profit_factor': profit_factor,
        'max_dd': max_dd
    }
    return trades, metrics

# ==================== OPTIMIZACIÓN POR DIRECCIÓN Y TIMEFRAME ====================
def optimizar_direccion(df, timeframe, direction):
    """Optimiza parámetros para una dirección y timeframe específicos."""
    print(f"\n{'='*60}")
    print(f"OPTIMIZANDO {direction.upper()} en {timeframe}")
    print(f"{'='*60}")

    # Definir rangos según dirección
    if direction == 'long':
        threshold_range = RSI_LOW_RANGE
    else:
        threshold_range = RSI_HIGH_RANGE

    # Generar todas las combinaciones
    combinations = list(product(
        PERIOD_RANGE, PERIOD_RANGE, PERIOD_RANGE,  # adx_period, rsi_period, atr_period
        ADX_TH_RANGE, threshold_range,
        MULT_STOP_RANGE, MULT_TP_RANGE,
        USE_SLOPE_OPTIONS
    ))
    total = len(combinations)
    print(f"Total combinaciones a probar: {total}")
    print(f"Progreso: 0%", end='')

    mejor_profit = -np.inf
    mejor_params = None
    mejores_metricas = None

    for idx, (adx_p, rsi_p, atr_p, adx_th, rsi_th, mult_stop, mult_tp, use_slope) in enumerate(combinations):
        # Mostrar progreso cada 5%
        if (idx + 1) % max(1, total // 20) == 0:
            pct = (idx + 1) / total * 100
            print(f"\rProgreso: {pct:.1f}%", end='')

        params = {
            'adx_period': adx_p,
            'rsi_period': rsi_p,
            'atr_period': atr_p,
            'adx_th': adx_th,
            'rsi_th': rsi_th,
            'mult_stop': mult_stop,
            'mult_tp': mult_tp,
            'use_slope': use_slope
        }

        _, metrics = backtest_direction(df, direction, params, capital=1, leverage=1)

        if metrics['profit'] > mejor_profit:
            mejor_profit = metrics['profit']
            mejor_params = params.copy()
            mejores_metricas = metrics

    print(f"\rProgreso: 100% - Completado")

    # Mostrar mejores resultados
    print(f"\n>>> MEJORES PARÁMETROS para {direction} en {timeframe} <<<")
    print(f"  ADX período: {mejor_params['adx_period']}")
    print(f"  RSI período: {mejor_params['rsi_period']}")
    print(f"  ATR período: {mejor_params['atr_period']}")
    print(f"  ADX umbral: {mejor_params['adx_th']}")
    print(f"  RSI umbral: {mejor_params['rsi_th']}")
    print(f"  Mult Stop: {mejor_params['mult_stop']}")
    print(f"  Mult TP: {mejor_params['mult_tp']}")
    print(f"  Usar pendiente ADX: {mejor_params['use_slope']}")
    print(f"  Profit: {mejores_metricas['profit']:.4f}")
    print(f"  Trades: {mejores_metricas['trades']}")
    print(f"  Win Rate: {mejores_metricas['win_rate']*100:.2f}%")
    print(f"  Profit Factor: {mejores_metricas['profit_factor']:.2f}")
    print(f"  Max DD: {mejores_metricas['max_dd']*100:.2f}%")

    return mejor_params, mejores_metricas

# ==================== ASIGNACIÓN DE CAPITAL ====================
def calcular_asignacion_capital(win_rate_long, win_rate_short):
    """Calcula porcentajes de capital para long y short basado en win rates."""
    total = win_rate_long + win_rate_short
    if total == 0:
        return 50.0, 50.0
    pct_long = (win_rate_long / total) * 100
    pct_short = 100 - pct_long
    return round(pct_long, 2), round(pct_short, 2)

# ==================== CÁLCULO DE APALANCAMIENTO ÓPTIMO ====================
def calcular_apalancamiento(win_rate):
    """Calcula apalancamiento sugerido basado en win rate."""
    if win_rate > MIN_WIN_RATE_FOR_LEVERAGE:
        return min(MAX_LEVERAGE, int(MAX_LEVERAGE * (win_rate / 0.5)))
    else:
        return 1

# ==================== OPTIMIZACIÓN COMPLETA ====================
def optimizar_todo():
    """Ejecuta optimización para todos los timeframes y direcciones."""
    print("\n" + "="*70)
    print("OPTIMIZACIÓN DE ESTRATEGIA DUAL PARA ETHUSDT")
    print("="*70)
    
    # Descargar datos con failover automático
    df_1m = fetch_klines_with_fallback(SYMBOL, INTERVAL_BASE, HOURS)
    if df_1m is None or df_1m.empty:
        print("❌ Error: No se pudieron descargar datos de ninguna fuente.")
        return None
        
    print(f"✅ Velas de 1m descargadas: {len(df_1m)}")
    print("Reagrupando a timeframes superiores...")

    # Reagrupar a los timeframes necesarios
    dfs = {}
    for nombre, rule in TIMEFRAMES.items():
        dfs[nombre] = resample_ohlc(df_1m, rule)
        print(f"  {nombre}: {len(dfs[nombre])} velas")

    # Diccionario para guardar mejores resultados por timeframe
    mejores_resultados = {}

    # Optimizar para cada timeframe
    for tf in TIMEFRAMES.keys():
        print(f"\n{'#'*60}")
        print(f"# TIMEFRAME: {tf}")
        print(f"{'#'*60}")
        df = dfs[tf]

        # Optimizar long
        best_long, metrics_long = optimizar_direccion(df, tf, 'long')
        # Optimizar short
        best_short, metrics_short = optimizar_direccion(df, tf, 'short')

        if best_long and best_short:
            # Calcular asignación de capital
            pct_long, pct_short = calcular_asignacion_capital(
                metrics_long['win_rate'], metrics_short['win_rate']
            )
            # Calcular apalancamiento sugerido
            leverage_long = calcular_apalancamiento(metrics_long['win_rate'])
            leverage_short = calcular_apalancamiento(metrics_short['win_rate'])

            # Guardar en diccionario
            mejores_resultados[tf] = {
                'long': {
                    'params': best_long,
                    'metrics': metrics_long,
                    'capital_pct': pct_long,
                    'leverage': leverage_long
                },
                'short': {
                    'params': best_short,
                    'metrics': metrics_short,
                    'capital_pct': pct_short,
                    'leverage': leverage_short
                }
            }
        else:
            print(f"  No se encontraron parámetros óptimos para {tf}")

    # Mostrar resumen final
    print("\n" + "="*70)
    print("RESUMEN FINAL DE OPTIMIZACIÓN")
    print("="*70)

    for tf, res in mejores_resultados.items():
        print(f"\n>>> TIMEFRAME: {tf} <<<")
        print("-" * 50)
        print("LONG:")
        print(f"  Parámetros: ADX={res['long']['params']['adx_period']}, RSI={res['long']['params']['rsi_period']}, ATR={res['long']['params']['atr_period']}")
        print(f"  Umbral ADX: {res['long']['params']['adx_th']}, RSI < {res['long']['params']['rsi_th']}")
        print(f"  Mult Stop/TP: {res['long']['params']['mult_stop']}/{res['long']['params']['mult_tp']}, Pendiente: {res['long']['params']['use_slope']}")
        print(f"  Win Rate: {res['long']['metrics']['win_rate']*100:.2f}%")
        print(f"  Profit Factor: {res['long']['metrics']['profit_factor']:.2f}")
        print(f"  Trades: {res['long']['metrics']['trades']}")
        print(f"  Profit: {res['long']['metrics']['profit']:.4f}")
        print(f"  Max DD: {res['long']['metrics']['max_dd']*100:.2f}%")
        print(f"  Asignación capital: {res['long']['capital_pct']}%")
        print(f"  Apalancamiento sugerido: {res['long']['leverage']}x")

        print("\nSHORT:")
        print(f"  Parámetros: ADX={res['short']['params']['adx_period']}, RSI={res['short']['params']['rsi_period']}, ATR={res['short']['params']['atr_period']}")
        print(f"  Umbral ADX: {res['short']['params']['adx_th']}, RSI > {res['short']['params']['rsi_th']}")
        print(f"  Mult Stop/TP: {res['short']['params']['mult_stop']}/{res['short']['params']['mult_tp']}, Pendiente: {res['short']['params']['use_slope']}")
        print(f"  Win Rate: {res['short']['metrics']['win_rate']*100:.2f}%")
        print(f"  Profit Factor: {res['short']['metrics']['profit_factor']:.2f}")
        print(f"  Trades: {res['short']['metrics']['trades']}")
        print(f"  Profit: {res['short']['metrics']['profit']:.4f}")
        print(f"  Max DD: {res['short']['metrics']['max_dd']*100:.2f}%")
        print(f"  Asignación capital: {res['short']['capital_pct']}%")
        print(f"  Apalancamiento sugerido: {res['short']['leverage']}x")

    return mejores_resultados

# ==================== SIMULACIÓN LIVE (OPCIONAL) ====================
def live_simulation(mejores_resultados):
    """Simula un bucle de trading en vivo usando los mejores parámetros."""
    print("\n" + "="*70)
    print("MODO SIMULACIÓN LIVE (usando mejores parámetros)")
    print("="*70)
    print("⚠️  Esta es una simulación - No se ejecutan órdenes reales")
    
    try:
        ciclo = 0
        while True:
            ciclo += 1
            print(f"\n[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] Ciclo {ciclo} - Evaluando señales...")
            
            # Descargar datos más recientes para simulación
            df_1m = fetch_klines_with_fallback(SYMBOL, INTERVAL_BASE, 1)  # Solo 1 hora para simulación rápida
            if df_1m is not None:
                for tf in TIMEFRAMES.keys():
                    if tf not in mejores_resultados:
                        continue
                    df_tf = resample_ohlc(df_1m, TIMEFRAMES[tf])
                    if len(df_tf) > 0:
                        last_row = df_tf.iloc[-1]
                        print(f"  {tf} - Último precio: {last_row['close']:.2f}")
                        
                        for direction in ['long', 'short']:
                            params = mejores_resultados[tf][direction]['params']
                            # Calcular condiciones básicas (simplificado)
                            print(f"    {direction}: umbral RSI={params['rsi_th']}, mult_stop={params['mult_stop']}")
            
            # Esperar 60 segundos para el siguiente ciclo
            print(f"  Esperando 60 segundos... (Ctrl+C para salir)")
            time.sleep(60)
            
    except KeyboardInterrupt:
        print("\n\n✅ Simulación finalizada por el usuario.")
    except Exception as e:
        print(f"\n❌ Error en simulación: {e}")

# ==================== PROGRAMA PRINCIPAL ====================
def main():
    # Si se pasa el argumento 'live', mostrar mensaje (requiere optimización previa)
    if len(sys.argv) > 1 and sys.argv[1] == 'live':
        print("⚠️  Modo live requiere optimización previa.")
        print("Ejecute primero sin argumentos para optimizar.")
        return
    else:
        # En cualquier otro caso, ejecutar optimización
        print("\n🚀 Iniciando optimización de parámetros para Ethereum...")
        print("(Los datos se descargarán automáticamente de Binance, Bybit o Bitget)")
        resultados = optimizar_todo()
        
        if resultados:
            # Preguntar si desea simulación live
            print("\n" + "="*70)
            resp = input("\n¿Desea ejecutar simulación LIVE con estos parámetros? (s/n): ").strip().lower()
            if resp == 's':
                live_simulation(resultados)
            else:
                print("\n✅ Optimización completada. Puede revisar los resultados arriba.")
        else:
            print("\n❌ No se pudo completar la optimización.")

if __name__ == "__main__":
    main()
