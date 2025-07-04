import json
import math
import uuid
import threading
import pandas as pd
from collections import deque
from datetime import datetime, time, date
from kafka import KafkaConsumer
from dash import Dash, dcc, html
from dash.dependencies import Input, Output
import plotly.graph_objs as go
from statsmodels.nonparametric.smoothers_lowess import lowess

SUNRISE = time(4, 29)
SUNSET = time(20, 45)

wind_accessible = 1000
panels_accessible = 1000

TOLERANCE_YELLOW = 0.015
TOLERANCE_ORANGE = 0.02
WINDOW_SIZE = 5

high_error_count = 0
wind_history = deque(maxlen=WINDOW_SIZE)
panel_history = deque(maxlen=WINDOW_SIZE)
wind_diffs = deque(maxlen=60)
panel_diffs = deque(maxlen=60)
error_history = deque(maxlen=60)

auto_group = f"my_group_{uuid.uuid4()}"

orange_count = 0
red_mode = False
red_cooldown = 0
RED_TRIGGER = 3
RED_RECOVERY = 5

data_buffer = deque(maxlen=60)  
log_buffer = deque(maxlen=50)  
data_history = [] 

def log(message):
    timestamped = f"{datetime.now().isoformat()} | {message}"
    print(timestamped)
    log_buffer.appendleft(timestamped)

def loess_smoothing(data):
    if len(data) < 3:
        return data[-1]
    result = lowess(data, range(len(data)), frac=0.5)
    return result[-1][1]

def kafka_consumer():
    global orange_count, red_mode, red_cooldown, high_error_count
    consumer = KafkaConsumer(
        "energy_input_stream",
        bootstrap_servers='localhost:9092',
        auto_offset_reset='latest',
        group_id=auto_group,
        value_deserializer=lambda v: json.loads(v.decode('utf-8'))
    )

    log(f"Uruchomiono konsumenta z group_id: {auto_group}")

    for msg in consumer:
        data = msg.value
        try:
            ts = datetime.fromisoformat(data['timestamp'])
            current_time = ts.time()

            demand_left = data['power_demand_mw'] - data['coal_turbine_power_mw']

            if demand_left <= 0:
                wind_needed = 0
                panels_needed = 0
            else:
                wind_capacity = data.get('wind_turbine_power_mw', 0)
                panel_capacity = data.get('solar_power_mw', 0)

                if current_time < SUNRISE or current_time > SUNSET or wind_capacity <= 0:
                    wind_energy_target = demand_left
                    solar_energy_target = 0
                else:
                    wind_energy_target = 0.75 * demand_left
                    solar_energy_target = 0.25 * demand_left

                wind_needed_raw = math.ceil(wind_energy_target / wind_capacity) if wind_capacity > 0 else 0
                panels_needed_raw = math.ceil(solar_energy_target / panel_capacity) if panel_capacity > 0 else 0

                wind_needed_raw = min(wind_needed_raw, wind_accessible)
                panels_needed_raw = min(panels_needed_raw, panels_accessible)

                if not red_mode:
                    wind_history.append(wind_needed_raw)
                    panel_history.append(panels_needed_raw)
                    wind_needed = round(loess_smoothing(wind_history))
                    panels_needed = round(loess_smoothing(panel_history))
                    log("użycie LOWESS")
                else:
                    wind_needed = wind_needed_raw
                    panels_needed = panels_needed_raw
            if wind_history and len(wind_history) > 1:
                wind_diffs.append(abs(wind_needed - wind_history[-2]))
            if panel_history and len(panel_history) > 1:
                panel_diffs.append(abs(panels_needed - panel_history[-2]))

            energy_produced = wind_needed * wind_capacity + panels_needed * panel_capacity
            error = (energy_produced - demand_left) / demand_left if demand_left > 0 else 0
            error_abs = abs(error)
            error_history.append(error_abs)


            log(
                f"{data['timestamp']} | wind_turbine_amount: {wind_needed} | "
                f"solar_panel_amount: {panels_needed} | error: {error:.3%}"
            )


            energy_produced_overall = energy_produced + data['coal_turbine_power_mw'] \
            data_point = {
                "time": ts,
                "demand": round(data['power_demand_mw'], 2),
                "energy_produced_overall": round(energy_produced_overall, 2)
            }
            data_buffer.append(data_point) 
            data_history.append(data_point)

            if error_abs >= TOLERANCE_ORANGE:
                high_error_count += 1
                log("ALERT POMARAŃCZOWY: Niedokładność >= 2%")
                orange_count += 1
                if orange_count >= RED_TRIGGER and not red_mode:
                    red_mode = True
                    red_cooldown = 0
                    log("ALERT CZERWONY: Tryb precyzyjnego dopasowania aktywowany!")
            elif error_abs >= TOLERANCE_YELLOW:
                log("ALERT ŻÓŁTY: Niedokładność >= 1.5%")
                orange_count = 0  
            else:
                orange_count = 0  

            if red_mode and error_abs < TOLERANCE_YELLOW:
                red_cooldown += 1
                if red_cooldown >= RED_RECOVERY:
                    red_mode = False
                    log("Tryb precyzyjny wyłączony — powrót do średniej kroczącej.")
                    wind_history.clear()
                    panel_history.clear()
                    orange_count = 0
            elif red_mode:
                red_cooldown = 0  

        except Exception as e:
            log(f"Błąd przetwarzania komunikatu: {e}")
            continue

app = Dash(__name__)
app.layout = html.Div([
    html.H2("Live Energy Monitoring Dashboard"),
    dcc.Graph(id='live-graph'),
    html.Div(id='difference-output', style={'marginTop': '20px', 'fontSize': '18px', 'color': '#333'}),
    dcc.Interval(id='interval-component', interval=2000, n_intervals=0),
    html.H4("Log Output"),
    html.Pre(id='log-output', style={'height': '300px', 'overflowY': 'scroll', 'backgroundColor': '#f4f4f4'}),
    html.Div([
        html.Label('Start time (HH:MM:SS)'),
        dcc.Input(id = 'start-time', type = 'text', placeholder = 'HH:MM:SS'),
        html.Label('End time (HH:MM:SS)'),
        dcc.Input(id = 'end-time', type = 'text', placeholder = 'HH:MM:SS'),
        html.Button('Save selected timeframe data to CSV', id = 'save-button'),
        html.Div(id = 'save-status')])
])

@app.callback(
    Output('live-graph', 'figure'),
    Output('log-output', 'children'),
    Output('difference-output', 'children'),
    Input('interval-component', 'n_intervals')
)

def update_dashboard(n):

    if data_buffer:
        times = [d['time'] for d in data_buffer]
        demand = [d['demand'] for d in data_buffer]
        production = [d['energy_produced_overall'] for d in data_buffer]
        demand_minus_2 = [d*0.98 for d in demand]
        demand_plus_2 = [d*1.02 for d in demand]

        fig = go.Figure()
        fig.add_trace(go.Scatter(x=times, y=demand, mode='lines+markers', name='Energy Demand', line=dict(color='red')))
        fig.add_trace(go.Scatter(x=times, y=production, mode='lines+markers', name='Total Energy Produced', line=dict(color='green')))
        
        # Pasek tolerancji +/- 2%
        fig.add_trace(go.Scatter(x = times, y = demand_plus_2, mode='lines', line = dict(width=0), showlegend = False, hoverinfo = 'skip'))
        fig.add_trace(go.Scatter(x = times, 
                                 y = demand_minus_2, 
                                 mode='lines', 
                                 fill = 'tonexty', 
                                 fillcolor= 'rgba(144, 238, 144, 0.3)', 
                                 line = dict(width=0), 
                                 showlegend = False, 
                                 hoverinfo = 'skip'))
        
        fig.update_layout(
            title='Energy Demand vs Production (Live)',
            xaxis_title='Time',
            yaxis_title='Power (MW)',
            xaxis=dict(showgrid=True, tickangle=45),
            yaxis=dict(showgrid=True),
            legend=dict(x=0.01, y=0.99),
            margin=dict(l=40, r=40, t=40, b=40),
            height=500,
            hovermode='x unified'
        )
    else:
        fig = go.Figure()


    logs = "\n".join(log_buffer)
    wind_avg_diff = sum(wind_diffs) / len(wind_diffs) if wind_diffs else 0
    panel_avg_diff = sum(panel_diffs) / len(panel_diffs) if panel_diffs else 0
    avg_error = sum(error_history) / len(error_history) if error_history else 0


    diff_text = (
    f"Średnia zmiana turbin: {wind_avg_diff:.2f} | "
    f"Średnia zmiana paneli: {panel_avg_diff:.2f} | "
    f"Średni błąd dopasowania: {avg_error:.2%} | "
    f"Liczba przekroczeń 2%: {high_error_count}"
)

    return fig, logs, diff_text

@app.callback(
    Output('save-status','children'),
    Input('save-button', 'n_clicks'),
    [Input('start-time','value'),
     Input('end-time','value')]
    )

def save_data(n_clicks, start_str, end_str):
    if not n_clicks or not start_str or not end_str:
        return ''
    
    try:
        start = pd.to_datetime(f'2023-06-15 {start_str}')
        end = pd.to_datetime(f'2023-06-15 {end_str}')
        
        data_history_df = pd.DataFrame(data_history)
        data_history_df['time'] = pd.to_datetime(data_history_df['time'])
        filtered = data_history_df[(data_history_df['time'] >= start) & (data_history_df['time'] <= end)]
        
        if filtered.empty:
            return 'Brak danych w wybranym przedziale czasowym'
        
        filename = f'energy_data_{date.today()}-{start.strftime("%H%M%S")}-{end.strftime("%H%M%S")}.csv'
        filtered.to_csv(filename, index = False)
        return f'Zapisano do pliku {filename}'
    except Exception as e:
        return f'Błąd zapisu: {str(e)}'

threading.Thread(target=kafka_consumer, daemon=True).start()

if __name__ == '__main__':
    try:
        app.run(debug=True, use_reloader=False, port=8050)
    except KeyboardInterrupt:
        print("Shutting down.")
