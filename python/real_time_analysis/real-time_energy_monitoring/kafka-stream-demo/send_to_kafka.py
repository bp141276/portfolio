import pandas as pd
import json
import time
from kafka import KafkaProducer
from datetime import datetime
from datetime import timedelta

print("Start wysyłania do Kafki")

# Wczytanie danych i konwersja kolumny timestamp do datetime
df = pd.read_csv("simulated_input_data.csv", parse_dates=['timestamp'])

producer = KafkaProducer(
    bootstrap_servers='localhost:9092',
    value_serializer=lambda v: json.dumps(v).encode('utf-8')
)

while True:
    now = datetime.now().replace(microsecond=0)
 
    # Odejmij  godziny
    one_hour_earlier = now - timedelta(hours=4)
 
    # Zaokrąglij sekundy do najbliższej 5 w dół
    seconds = (one_hour_earlier.second // 5) * 5
    current_time_rounded = one_hour_earlier.replace(second=seconds)
 
    # Zwróć tylko część czasową
    current_time_only = current_time_rounded.time()
    # Filtruj wiersze, które mają ten sam czas (ignorując datę)
    mask = df['timestamp'].dt.time == current_time_rounded.time()

    # Filtruj wiersze, które mają ten sam czas (ignorując datę
    row = df[mask]

    if not row.empty:
        message = row.iloc[0].to_dict()
        # Konwersja Timestamp na string ISO format
        message['timestamp'] = message['timestamp'].isoformat()

        producer.send("energy_input_stream", value=message).add_callback
        
        print("Wysłano:", message)
    else:
        print(f"Brak danych dla czasu {current_time_rounded}")

    time.sleep(5)