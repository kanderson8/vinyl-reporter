import os
import csv
import io
import time
from datetime import timedelta
from flask import Flask, render_template, request, jsonify, session, redirect, url_for
from openai import OpenAI
import json

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'dev-secret-key-change-in-production')
app.permanent_session_lifetime = timedelta(hours=1)

oauth_token_cache = {}

DISCOGS_CONSUMER_KEY = os.environ.get('DISCOGS_CONSUMER_KEY')
DISCOGS_CONSUMER_SECRET = os.environ.get('DISCOGS_CONSUMER_SECRET')
DISCOGS_USER_AGENT = os.environ.get('DISCOGS_USER_AGENT', 'RecordCollectionAnalyzer/1.0')
DISCOGS_CALLBACK_URL = os.environ.get('DISCOGS_CALLBACK_URL', 'http://localhost:5000/callback')

def get_openai_client():
    """Get or create OpenAI client."""
    api_key = os.environ.get('OPENAI_API_KEY')
    if not api_key:
        raise ValueError("OPENAI_API_KEY environment variable is not set")
    return OpenAI(api_key=api_key)

def get_discogs_client():
    """Get Discogs client with user tokens if available."""
    try:
        import discogs_client
        client = discogs_client.Client(
            DISCOGS_USER_AGENT,
            consumer_key=DISCOGS_CONSUMER_KEY,
            consumer_secret=DISCOGS_CONSUMER_SECRET
        )
        
        if session.get('discogs_token') and session.get('discogs_token_secret'):
            client.set_token(
                session['discogs_token'],
                session['discogs_token_secret']
            )
        
        return client
    except ImportError:
        raise ValueError("discogs-client library not installed. Run: pip install discogs-client")

def parse_discogs_csv(file_content):
    """Parse Discogs CSV file and extract collection data."""
    collection_data = []
    try:
        if isinstance(file_content, bytes):
            file_content = file_content.decode('utf-8')
        
        csv_reader = csv.DictReader(io.StringIO(file_content))
        for row in csv_reader:
            album_info = {
                'artist': row.get('Artist', row.get('artist', '')),
                'album': row.get('Album', row.get('album', row.get('Title', ''))),
                'label': row.get('Label', row.get('label', '')),
                'year': row.get('Year', row.get('year', '')),
                'genre': row.get('Genre', row.get('genre', '')),
                'format': row.get('Format', row.get('format', ''))
            }
            collection_data.append(album_info)
    except Exception as e:
        raise ValueError(f"Error parsing CSV: {str(e)}")
    
    return collection_data

def fetch_collection_from_discogs():
    """Fetch user's collection from Discogs API."""
    try:
        client = get_discogs_client()
        user = client.identity()
        
        collection_data = []
        
        try:
            folders = user.collection_folders
        except Exception as e:
            raise ValueError(f"Could not access collection folders: {str(e)}")
        
        if not folders or len(folders) == 0:
            return [], user.username
        
        for folder in folders:
            try:
                releases = folder.releases
            except Exception as e:
                continue
            
            for item in releases:
                try:
                    release = item.release
                    artists = [artist.name for artist in release.artists] if release.artists else []
                    artist_name = ', '.join(artists) if artists else 'Unknown Artist'
                    
                    genres = release.genres if hasattr(release, 'genres') and release.genres else []
                    genre = ', '.join(genres) if genres else ''
                    
                    styles = release.styles if hasattr(release, 'styles') and release.styles else []
                    style = ', '.join(styles) if styles else ''
                    
                    labels = [label.name for label in release.labels] if hasattr(release, 'labels') and release.labels else []
                    label_name = ', '.join(labels) if labels else ''
                    
                    formats = []
                    if hasattr(release, 'formats') and release.formats:
                        for f in release.formats:
                            if isinstance(f, dict):
                                formats.append(f.get('name', ''))
                            elif hasattr(f, 'name'):
                                formats.append(f.name)
                    format_str = ', '.join([f for f in formats if f]) if formats else ''
                    
                    album_info = {
                        'artist': artist_name,
                        'album': release.title,
                        'label': label_name,
                        'year': str(release.year) if release.year else '',
                        'genre': genre or style,
                        'format': format_str
                    }
                    collection_data.append(album_info)
                    
                    time.sleep(0.25)
                    
                except Exception:
                    continue
        
        return collection_data, user.username
    
    except Exception as e:
        raise ValueError(f"Error fetching collection from Discogs: {str(e)}")

def analyze_collection_with_llm(collection_data):
    """Use OpenAI to analyze the collection and generate insights."""
    collection_summary = []
    for item in collection_data[:100]:
        summary_line = f"{item['artist']} - {item['album']}"
        if item.get('year'):
            summary_line += f" ({item['year']})"
        if item.get('genre'):
            summary_line += f" [{item['genre']}]"
        collection_summary.append(summary_line)
    
    collection_text = "\n".join(collection_summary)
    total_albums = len(collection_data)
    
    prompt = f"""You are a music collection analyst. Analyze the following record collection and provide insights.

Collection ({total_albums} albums):
{collection_text}

Please provide a JSON response with the following structure:
{{
    "vibe_summary": "One paragraph describing the collection's overall vibe and point of view",
    "strengths": "One paragraph describing the strengths of this collection",
    "improvements": "One paragraph describing areas where the collection could be improved",
    "recommendations": [
        "Album 1 - Artist 1",
        "Album 2 - Artist 2",
        "Album 3 - Artist 3",
        "Album 4 - Artist 4",
        "Album 5 - Artist 5"
    ]
}}

Be specific and insightful. Reference specific artists, genres, or eras when relevant."""

    try:
        client = get_openai_client()
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": "You are a knowledgeable music critic and collection analyst. Provide thoughtful, specific insights about record collections."},
                {"role": "user", "content": prompt}
            ],
            temperature=0.7,
            response_format={"type": "json_object"}
        )
        
        analysis = json.loads(response.choices[0].message.content)
        return analysis
    except Exception as e:
        raise ValueError(f"Error calling LLM: {str(e)}")

@app.route('/')
def index():
    """Main page with Discogs login or file upload."""
    is_authenticated = bool(session.get('discogs_token') and session.get('discogs_token_secret'))
    username = session.get('discogs_username', '')
    return render_template('index.html', is_authenticated=is_authenticated, username=username)

@app.route('/login')
def login():
    """Initiate Discogs OAuth flow."""
    if not DISCOGS_CONSUMER_KEY or not DISCOGS_CONSUMER_SECRET:
        return jsonify({'error': 'Discogs API credentials not configured'}), 500
    
    if not DISCOGS_CALLBACK_URL:
        return jsonify({'error': 'Discogs callback URL not configured'}), 500
    
    try:
        session.permanent = True
        
        client = get_discogs_client()
        request_token, request_secret, authorize_url = client.get_authorize_url(callback_url=DISCOGS_CALLBACK_URL)
        
        session['discogs_request_token'] = request_token
        session['discogs_request_secret'] = request_secret
        
        oauth_token_cache[request_token] = {
            'request_secret': request_secret,
            'timestamp': time.time()
        }
        
        session.modified = True
        
        return redirect(authorize_url)
    except Exception as e:
        return jsonify({'error': f'Error initiating login: {str(e)}'}), 500

@app.route('/callback')
def callback():
    """Handle OAuth callback from Discogs."""
    session.permanent = True
    
    verifier = request.args.get('oauth_verifier')
    oauth_token = request.args.get('oauth_token')
    
    if not verifier:
        error_msg = 'Authorization failed. No verifier received.'
        if oauth_token:
            error_msg += f' Received oauth_token: {oauth_token}'
        return render_template('error.html', message=error_msg), 400
    
    try:
        request_token = session.get('discogs_request_token')
        request_secret = session.get('discogs_request_secret')
        
        if not request_token or not request_secret:
            if oauth_token:
                cached_data = oauth_token_cache.get(oauth_token)
                if cached_data:
                    request_token = oauth_token
                    request_secret = cached_data['request_secret']
                    oauth_token_cache.clear()
        
        if not request_token or not request_secret:
            return render_template('error.html', message='Session expired. Please try logging in again.'), 400
        
        if oauth_token and oauth_token != request_token:
            return render_template('error.html', message='Token mismatch. Please try logging in again.'), 400
        
        import discogs_client
        client = discogs_client.Client(
            DISCOGS_USER_AGENT,
            consumer_key=DISCOGS_CONSUMER_KEY,
            consumer_secret=DISCOGS_CONSUMER_SECRET
        )
        
        try:
            client.set_token(request_token, request_secret)
            access_token, access_secret = client.get_access_token(verifier)
        except Exception as token_error:
            error_str = str(token_error).lower()
            if '401' in error_str or 'unauthorized' in error_str:
                return render_template('error.html', message='Authentication failed. Please check your Discogs API credentials.'), 401
            elif 'token' in error_str:
                return render_template('error.html', message='Token error. Please try logging in again.'), 400
            else:
                return render_template('error.html', message=f'Error getting access token: {str(token_error)}'), 500
        
        session['discogs_token'] = access_token
        session['discogs_token_secret'] = access_secret
        
        client.set_token(access_token, access_secret)
        try:
            user = client.identity()
            session['discogs_username'] = user.username
        except Exception:
            session['discogs_username'] = 'User'
        
        session.pop('discogs_request_token', None)
        session.pop('discogs_request_secret', None)
        
        return redirect(url_for('index'))
    except Exception as e:
        error_msg = f'Error completing authentication: {str(e)}'
        if '401' in str(e) or 'Unauthorized' in str(e):
            error_msg += '\n\nPlease check your DISCOGS_CONSUMER_KEY and DISCOGS_CONSUMER_SECRET.'
        elif 'token' in str(e).lower():
            error_msg += '\n\nPlease try logging in again.'
        return render_template('error.html', message=error_msg), 500

@app.route('/logout')
def logout():
    """Log out from Discogs."""
    session.pop('discogs_token', None)
    session.pop('discogs_token_secret', None)
    session.pop('discogs_username', None)
    return redirect(url_for('index'))

@app.route('/generate-report', methods=['POST'])
def generate_report():
    """Generate report from Discogs collection."""
    if not session.get('discogs_token') or not session.get('discogs_token_secret'):
        return jsonify({'error': 'Not authenticated with Discogs'}), 401
    
    try:
        collection_data, username = fetch_collection_from_discogs()
        
        if not collection_data:
            return jsonify({
                'error': 'No collection data found. Your Discogs collection appears to be empty, or there was an issue accessing it.'
            }), 400
        
        analysis = analyze_collection_with_llm(collection_data)
        
        session['analysis'] = analysis
        session['collection_size'] = len(collection_data)
        
        return jsonify({
            'success': True,
            'analysis': analysis,
            'collection_size': len(collection_data)
        })
    
    except ValueError as e:
        return jsonify({'error': str(e)}), 400
    except Exception as e:
        return jsonify({'error': f'Unexpected error: {str(e)}'}), 500

@app.route('/upload', methods=['POST'])
def upload_file():
    """Handle CSV file upload and generate analysis."""
    if 'file' not in request.files:
        return jsonify({'error': 'No file provided'}), 400
    
    file = request.files['file']
    if file.filename == '':
        return jsonify({'error': 'No file selected'}), 400
    
    if not file.filename.endswith('.csv'):
        return jsonify({'error': 'Please upload a CSV file'}), 400
    
    try:
        file_content = file.read()
        collection_data = parse_discogs_csv(file_content)
        
        if not collection_data:
            return jsonify({'error': 'CSV file appears to be empty or invalid'}), 400
        
        analysis = analyze_collection_with_llm(collection_data)
        
        session['analysis'] = analysis
        session['collection_size'] = len(collection_data)
        
        return jsonify({
            'success': True,
            'analysis': analysis,
            'collection_size': len(collection_data)
        })
    
    except ValueError as e:
        return jsonify({'error': str(e)}), 400
    except Exception as e:
        return jsonify({'error': f'Unexpected error: {str(e)}'}), 500

@app.route('/results')
def results():
    """Display analysis results."""
    analysis = session.get('analysis')
    collection_size = session.get('collection_size', 0)
    
    if not analysis:
        return render_template('error.html', message='No analysis found. Please generate a report first.'), 400
    
    return render_template('results.html', analysis=analysis, collection_size=collection_size)

if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5000)
