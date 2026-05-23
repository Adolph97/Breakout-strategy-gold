from flask import Flask, request, jsonify
import logging
import json
from datetime import datetime
import threading
import time

# Global variable to store the latest webhook signal
latest_webhook_signal = {
    "direction": None,
    "price": None,
    "timestamp": None
}

# Lock for thread-safe access to the signal
signal_lock = threading.Lock()

def create_webhook_app():
    """Create and configure the Flask webhook receiver app"""
    app = Flask(__name__)

    @app.route('/webhook', methods=['POST'])
    def webhook():
        """Receive trading signals from TradingView"""
        global latest_webhook_signal

        try:
            data = request.get_json()

            if not data:
                return jsonify({"error": "No JSON data received"}), 400

            # Extract signal data
            direction = data.get('direction', '').upper()
            price = data.get('price', None)

            # Validate direction
            if direction not in ['BUY', 'SELL']:
                return jsonify({"error": "Invalid direction. Must be BUY or SELL"}), 400

            # Update the global signal (thread-safe)
            with signal_lock:
                latest_webhook_signal = {
                    "direction": direction,
                    "price": price,
                    "timestamp": datetime.now().isoformat()
                }

            logging.info(f"Received webhook signal: {direction} at {price}")

            return jsonify({"status": "OK", "signal": latest_webhook_signal}), 200

        except Exception as e:
            logging.error(f"Error processing webhook: {e}")
            return jsonify({"error": "Internal server error"}), 500

    @app.route('/signal', methods=['GET'])
    def get_signal():
        """Get the latest webhook signal"""
        with signal_lock:
            return jsonify(latest_webhook_signal)

    @app.route('/health', methods=['GET'])
    def health():
        """Health check endpoint"""
        return jsonify({"status": "healthy", "timestamp": datetime.now().isoformat()})

    return app

def get_latest_signal():
    """Get the latest webhook signal (thread-safe)"""
    with signal_lock:
        return latest_webhook_signal.copy()

def run_webhook_server(host='0.0.0.0', port=5000):
    """Run the webhook server"""
    app = create_webhook_app()
    logging.info(f"Starting webhook server on {host}:{port}")
    app.run(host=host, port=port, debug=False, use_reloader=False)

if __name__ == "__main__":
    # Configure logging
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )

    # Run the webhook server
    run_webhook_server()