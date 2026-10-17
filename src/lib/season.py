from datetime import datetime as date

def get_season():
    current_year_date = date.now()
    year = current_year_date.year if (current_year_date.month >= 2) else (current_year_date.year - 1)
    # Telemetry data is only available for completed seasons (up to 2025)
    return min(year, 2025)