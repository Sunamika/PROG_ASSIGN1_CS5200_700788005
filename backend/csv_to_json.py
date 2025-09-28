# csv_to_json.py
import pandas as pd


df = pd.read_csv('IMDB-Movie-Data.csv')
df.to_json('imdb.json', orient='records', indent=2)

