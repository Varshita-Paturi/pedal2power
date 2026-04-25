# SmartPedal (pedal2power)

SmartPedal is an IoT and Web Application designed to monitor, track, and predict electricity generated through a pedal-powered energy system. The project features a robust Python/Flask backend and an engaging real-time web dashboard, seamlessly integrating hardware (ESP32) and machine learning capabilities to motivate users and optimize their energy production.

## Key Features

- **Real-Time Dashboard**: An interactive, dynamic UI that reflects live energy generation data directly from the ESP32 hardware via WebSocket/Server-Sent Events.
- **Motivational UI & Education**: A dedicated real-time motivational section designed to improve user retention by providing immediate performance feedback and educational insights.
- **Session Persistence**: Maintains user state across different browser tabs, ensuring a seamless experience.
- **Secure Authentication**: Requires a fresh login on every server restart to maintain backend security.
- **Machine Learning Integration**: An automated ML pipeline that trains regression models to predict energy generation based on historical sensor data, enabling users to foresee their impact.
- **Hardware Integration**: Interfaces directly with ESP32 microcontrollers using serial bridge scripts to read real-time voltage/current metrics.

## Tech Stack

- **Backend**: Python, Flask, SQLAlchemy
- **Frontend**: HTML5, Vanilla CSS, JavaScript
- **Machine Learning**: Scikit-Learn (Regression Models), Pandas, NumPy
- **Hardware Communication**: ESP32, PySerial

## Setup & Installation

1. Clone the repository and navigate to the project directory:
   ```bash
   cd pedal2power
   ```

2. Install the required Python packages:
   ```bash
   pip install -r requirements.txt
   ```

3. (Optional) Run the vendor setup script to download necessary client-side libraries:
   ```bash
   python setup_vendor.py
   ```

4. Configure your serial port for the ESP32 connection in `config.py`.

5. Run the application:
   ```bash
   python app.py
   ```

## Project Structure Highlights

- `app.py`: Main Flask application entry point.
- `config.py`: Application and environment configuration.
- `esp32_serial_bridge.py`: Script to bridge serial data from the ESP32 to the backend.
- `ml/`: Contains machine learning pipelines (`ml_pipeline.py`) and inference logic (`inference.py`) to predict power output.
- `routes/`: Flask API and view routing.
- `templates/` & `static/`: HTML templates and static assets for the rich dashboard.
