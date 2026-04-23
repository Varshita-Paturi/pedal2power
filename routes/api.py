from flask import Blueprint, request, jsonify, abort
from flask_login import login_required, current_user
from models.models import db, PedalSession, SessionData
from ml.predictor import EnergyPredictor
from datetime import datetime, timedelta
import sqlalchemy as sa

api = Blueprint('api', __name__)

# Initialize ML Predictor
predictor = EnergyPredictor()

# Moving Average buffer
ma_buffer = {}

def get_moving_average(session_id, rpm, voltage, current):
    if session_id not in ma_buffer:
        ma_buffer[session_id] = []
    
    buffer = ma_buffer[session_id]
    buffer.append({'rpm': rpm, 'voltage': voltage, 'current': current})
    
    # Keep only last 5
    if len(buffer) > 5:
        buffer.pop(0)
    
    # Calculate average
    avg_rpm = sum(d['rpm'] for d in buffer) / len(buffer)
    avg_voltage = sum(d['voltage'] for d in buffer) / len(buffer)
    avg_current = sum(d['current'] for d in buffer) / len(buffer)
    
    return avg_rpm, avg_voltage, avg_current

@api.before_request
@login_required
def check_auth():
    pass

@api.route('/api/session/start', methods=['POST'])
def start_session():
    session = PedalSession(user_id=current_user.id, start_time=datetime.utcnow())
    db.session.add(session)
    db.session.commit()
    return jsonify({
        'session_id': session.id,
        'started_at': session.start_time.isoformat()
    })

@api.route('/api/session/data', methods=['POST'])
def session_data():
    data = request.json
    session_id = data.get('session_id')
    raw_rpm = float(data.get('rpm', 0))
    raw_voltage = float(data.get('voltage', 0))
    raw_current = float(data.get('current', 0))
    
    session = PedalSession.query.get_or_404(session_id)
    if session.user_id != current_user.id:
        abort(403)
    
    # Apply Moving Average filter
    smooth_rpm, smooth_voltage, smooth_current = get_moving_average(session_id, raw_rpm, raw_voltage, raw_current)
    
    # Store individual data point
    data_point = SessionData(
        session_id=session_id,
        raw_rpm=raw_rpm,
        raw_voltage=raw_voltage,
        raw_current=raw_current,
        smoothed_rpm=smooth_rpm,
        smoothed_voltage=smooth_voltage,
        smoothed_current=smooth_current,
        power_w=smooth_voltage * smooth_current
    )
    db.session.add(data_point)
        
    # Update session summary (using smoothed values)
    session._rpm_sum += smooth_rpm
    session._voltage_sum += smooth_voltage
    session._current_sum += smooth_current
    session._data_points += 1
    
    session.avg_rpm = session._rpm_sum / session._data_points
    session.avg_voltage = session._voltage_sum / session._data_points
    session.avg_current = session._current_sum / session._data_points
    
    # Update latest raw values
    session.raw_rpm = raw_rpm
    session.raw_voltage = raw_voltage
    session.raw_current = raw_current
    session.power_w = smooth_voltage * smooth_current
    session.last_updated = datetime.utcnow()
    
    db.session.commit()
    return jsonify({'status': 'ok'})

@api.route('/api/session/end', methods=['POST'])
def end_session():
    data = request.json
    session_id = data.get('session_id')
    
    session = PedalSession.query.get_or_404(session_id)
    if session.user_id != current_user.id:
        abort(403)
        
    session.end_time = datetime.utcnow()
    duration = (session.end_time - session.start_time).total_seconds()
    session.duration_seconds = int(duration)
    
    # energy_wh = avg_voltage * avg_current * (duration_seconds / 3600)
    session.energy_wh = session.avg_voltage * session.avg_current * (session.duration_seconds / 3600.0)
    # calories_burned = duration_seconds * 0.1
    session.calories_burned = session.duration_seconds * 0.1
    # co2_saved_g = energy_wh * 400
    session.co2_saved_g = session.energy_wh * 400
    
    db.session.commit()
    
    # Auto-train ML model if session count >= 5
    session_count = PedalSession.query.filter(PedalSession.end_time != None).count()
    if session_count >= 5:
        # Get all data points for training
        all_data = SessionData.query.all()
        predictor.train(all_data)
    
    return jsonify(session.to_dict())

@api.route('/api/ml/stats', methods=['GET'])
def ml_stats():
    # Get latest metrics for live prediction if available
    live_session = PedalSession.query.filter_by(user_id=current_user.id, end_time=None).first()
    predicted_power = 0
    if live_session and predictor.stats['model_ready']:
        _, rf_power = predictor.predict(live_session.avg_rpm, live_session.avg_voltage, live_session.avg_current)
        predicted_power = rf_power

    return jsonify({
        **predictor.stats,
        'predicted_power': predicted_power,
        'session_count': PedalSession.query.filter(PedalSession.end_time != None).count()
    })

@api.route('/api/ml/predict', methods=['GET'])
def ml_predict():
    rpm = float(request.args.get('rpm', 0))
    voltage = float(request.args.get('voltage', 0))
    current = float(request.args.get('current', 0))
    
    lr_p, rf_p = predictor.predict(rpm, voltage, current)
    
    return jsonify({
        'predicted_power_lr': lr_p or 0,
        'predicted_power_rf': rf_p or 0,
        'actual_power': voltage * current
    })

@api.route('/api/sessions', methods=['GET'])
def get_sessions():
    sessions = PedalSession.query.filter_by(user_id=current_user.id).order_by(PedalSession.start_time.desc()).all()
    return jsonify([s.to_dict() for s in sessions])

@api.route('/api/sessions/<int:session_id>', methods=['GET'])
def get_session(session_id):
    session = PedalSession.query.get_or_404(session_id)
    if session.user_id != current_user.id:
        abort(404)
    return jsonify(session.to_dict())

@api.route('/api/live', methods=['GET'])
def get_live():
    session = PedalSession.query.filter_by(user_id=current_user.id, end_time=None).order_by(PedalSession.start_time.desc()).first()
    if not session:
        return jsonify({'active': False})
    
    # Check connection status (received data in last 10 seconds)
    is_connected = False
    if session.last_updated:
        is_connected = (datetime.utcnow() - session.last_updated).total_seconds() < 10
    
    # Calculate current metrics (most recent average)
    return jsonify({
        'active': True,
        'session_id': session.id,
        'is_connected': is_connected,
        'metrics': {
            # Show latest raw values so the dashboard responds immediately
            'voltage': session.raw_voltage,
            'current': session.raw_current,
            'power_w': session.raw_voltage * session.raw_current,
        },
        'totals': {
            'energy_wh': session.avg_voltage * session.avg_current * ((datetime.utcnow() - session.start_time).total_seconds() / 3600.0),
            'duration': int((datetime.utcnow() - session.start_time).total_seconds()),
            'calories': (datetime.utcnow() - session.start_time).total_seconds() * 0.1
        }
    })

@api.route('/api/stats/summary', methods=['GET'])
def get_summary():
    sessions = PedalSession.query.filter(PedalSession.user_id == current_user.id, PedalSession.end_time != None).all()
    
    total_energy = sum(s.energy_wh for s in sessions)
    total_calories = sum(s.calories_burned for s in sessions)
    total_co2 = sum(s.co2_saved_g for s in sessions)
    
    # Calculate streak
    today = datetime.utcnow().date()
    streak = 0
    current_date = today
    
    while True:
        has_session = PedalSession.query.filter(
            PedalSession.user_id == current_user.id,
            PedalSession.end_time != None,
            sa.func.date(PedalSession.start_time) == current_date
        ).first()
        
        if has_session:
            streak += 1
            current_date -= timedelta(days=1)
        else:
            break
            
    return jsonify({
        'total_sessions': len(sessions),
        'total_energy_wh': total_energy,
        'total_calories': total_calories,
        'total_co2_saved_g': total_co2,
        'streak_days': streak
    })
