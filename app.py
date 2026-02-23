import os
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from flask import Flask, render_template, request, jsonify, session, redirect, url_for, Response, stream_with_context
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

# Cache for fetched Discogs collections, keyed by username.
# Each entry: {'data': [...], 'timestamp': float}
collection_cache = {}
COLLECTION_CACHE_TTL = 600  # seconds (10 minutes)

# Temporary store for analysis results from SSE streams.
# Flask's cookie-based session can't be written from inside a streaming
# generator (headers are sent before the body), so we stash the result
# here keyed by session ID and retrieve it in /results.
pending_analysis = {}

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

COLLECTION_CACHE_FILE = os.path.join(os.path.dirname(__file__), '.collection_cache.json')

def _load_cache_file():
    """Load the on-disk collection cache."""
    try:
        with open(COLLECTION_CACHE_FILE, 'r') as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}

def _save_cache_file(cache):
    """Write the collection cache to disk."""
    with open(COLLECTION_CACHE_FILE, 'w') as f:
        json.dump(cache, f)

def get_cached_collection(username):
    """Return cached collection data for username if fresh, else None."""
    # Check in-memory first, then fall back to disk.
    entry = collection_cache.get(username)
    if not entry:
        disk_cache = _load_cache_file()
        entry = disk_cache.get(username)
        if entry:
            collection_cache[username] = entry  # promote to memory
    if entry and (time.time() - entry['timestamp']) < COLLECTION_CACHE_TTL:
        return entry['data']
    return None

def set_cached_collection(username, data):
    """Store collection data in the cache (memory + disk)."""
    entry = {'data': data, 'timestamp': time.time()}
    collection_cache[username] = entry
    disk_cache = _load_cache_file()
    disk_cache[username] = entry
    _save_cache_file(disk_cache)

def fetch_collection_from_discogs():
    """Fetch user's collection from Discogs API, using cache if available."""
    try:
        client = get_discogs_client()
        user = client.identity()

        cached = get_cached_collection(user.username)
        if cached is not None:
            return cached, user.username

        collection_data = []

        try:
            folders = user.collection_folders
        except Exception as e:
            raise ValueError(f"Could not access collection folders: {str(e)}")

        if not folders or len(folders) == 0:
            return [], user.username

        # Folder 0 is the "All" folder containing every release.
        # Iterating all folders would double-count since other folders
        # are subsets of "All".
        all_folder = folders[0]
        releases = all_folder.releases

        for item in releases:
            try:
                data = item.release.data

                artists = [a['name'] for a in data.get('artists', [])] if data.get('artists') else []
                artist_name = ', '.join(artists) if artists else 'Unknown Artist'

                genres = data.get('genres', []) or []
                genre = ', '.join(genres) if genres else ''

                styles = data.get('styles', []) or []
                style = ', '.join(styles) if styles else ''

                labels = [l['name'] for l in data.get('labels', [])] if data.get('labels') else []
                label_name = ', '.join(labels) if labels else ''

                formats = []
                for f in (data.get('formats', []) or []):
                    if isinstance(f, dict):
                        formats.append(f.get('name', ''))
                format_str = ', '.join([f for f in formats if f]) if formats else ''

                album_info = {
                    'artist': artist_name,
                    'album': data.get('title', ''),
                    'label': label_name,
                    'year': str(data.get('year', '')) if data.get('year') else '',
                    'genre': genre or style,
                    'format': format_str
                }
                collection_data.append(album_info)

            except Exception:
                continue

        if collection_data:
            set_cached_collection(user.username, collection_data)

        return collection_data, user.username

    except Exception as e:
        raise ValueError(f"Error fetching collection from Discogs: {str(e)}")

MAX_COLLECTION_SIZE = 500

def _build_collection_text(collection_data):
    """Build the collection summary text for LLM prompts."""
    albums_to_analyze = collection_data[:MAX_COLLECTION_SIZE]
    collection_summary = []
    for item in albums_to_analyze:
        summary_line = f"{item['artist']} - {item['album']}"
        if item.get('year'):
            summary_line += f" ({item['year']})"
        if item.get('genre'):
            summary_line += f" [{item['genre']}]"
        collection_summary.append(summary_line)
    return "\n".join(collection_summary), len(albums_to_analyze)


def _call_llm(prompt):
    """Make a single LLM call and return parsed JSON."""
    client = get_openai_client()
    response = client.chat.completions.create(
        model="gpt-5.2",
        messages=[
            {"role": "system", "content": "You are a knowledgeable music critic and collection analyst. Provide thoughtful, specific insights about record collections."},
            {"role": "user", "content": prompt}
        ],
        temperature=0.7,
        response_format={"type": "json_object"}
    )
    return json.loads(response.choices[0].message.content)


def analyze_collection_with_llm(collection_data):
    """Use OpenAI to analyze the collection and generate insights."""
    collection_text, num_analyzed = _build_collection_text(collection_data)

    overview_prompt = f"""You are a music collection analyst. Analyze the following record collection.

Collection ({num_analyzed} albums):
{collection_text}

Please provide a JSON response with the following structure:
{{
    "vibe_summary": "One paragraph describing the collection's overall vibe and point of view",
    "strengths": "One paragraph describing the strengths of this collection",
    "taste_recommendations": [
        "Album 1 - Artist 1",
        "Album 2 - Artist 2",
        "Album 3 - Artist 3",
        "Album 4 - Artist 4",
        "Album 5 - Artist 5"
    ]
}}

INSTRUCTIONS:
- "vibe_summary": Describe the overall personality and point of view of this collection.
- "strengths": What this collection does well.
- "taste_recommendations": 5 albums the collector would LOVE based on their existing tastes. These should feel like natural extensions of what they already enjoy — not gap-filling or improvement-focused.

CRITICAL — RECOMMENDATION EXCLUSION RULE (DO NOT VIOLATE):
Every recommended album MUST NOT already appear in the collection listed above. Before finalizing your response, cross-check EVERY recommendation against the full collection list. If an album or artist/album pair is already in the collection, replace it with a different one. Recommending an album the user already owns destroys trust and is the single worst mistake you can make.

Be specific and insightful. Reference specific artists, genres, or eras when relevant."""

    growth_prompt = f"""You are a music collection analyst. Analyze the following record collection and suggest growth areas.

Collection ({num_analyzed} albums):
{collection_text}

Please provide a JSON response with the following structure:
{{
    "growth_areas": [
        {{
            "title": "2-5 word title",
            "description": "A paragraph explaining this area and why exploring it would enrich the collection",
            "recommendations": [
                "Album - Artist",
                "Album - Artist",
                "Album - Artist"
            ]
        }}
    ]
}}

INSTRUCTIONS:
- "growth_areas": Exactly 3 or 4 areas where the collection could expand. Each must have:
  - A short title (2-5 words)
  - A description paragraph explaining the area and why it would enrich the collection
  - Exactly 3 album recommendations for that area

CRITICAL — RECOMMENDATION EXCLUSION RULE (DO NOT VIOLATE):
Every recommended album MUST NOT already appear in the collection listed above. Before finalizing your response, cross-check EVERY recommendation against the full collection list. If an album or artist/album pair is already in the collection, replace it with a different one. Recommending an album the user already owns destroys trust and is the single worst mistake you can make. All recommendations must be unique (no duplicates).

Be specific and insightful. Reference specific artists, genres, or eras when relevant."""

    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            overview_future = executor.submit(_call_llm, overview_prompt)
            growth_future = executor.submit(_call_llm, growth_prompt)
            overview = overview_future.result()
            growth = growth_future.result()

        return {
            'vibe_summary': overview['vibe_summary'],
            'strengths': overview['strengths'],
            'taste_recommendations': overview['taste_recommendations'],
            'growth_areas': growth['growth_areas'],
        }
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

        if len(collection_data) > MAX_COLLECTION_SIZE:
            return jsonify({
                'error': f'Your collection has {len(collection_data)} albums. Collections over {MAX_COLLECTION_SIZE} albums are not currently supported.'
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

@app.route('/generate-report-stream')
def generate_report_stream():
    """Stream report generation progress via Server-Sent Events."""
    def event_stream():
        def send_status(step, message):
            data = json.dumps({'step': step, 'message': message})
            return f"event: status\ndata: {data}\n\n"

        def send_error(message):
            data = json.dumps({'message': message})
            return f"event: error_msg\ndata: {data}\n\n"

        def send_complete():
            return "event: complete\ndata: {}\n\n"

        # Step 1: Connect to Discogs
        yield send_status('step-connect', 'Connecting to Discogs...')

        if not session.get('discogs_token') or not session.get('discogs_token_secret'):
            yield send_error('Not authenticated with Discogs. Please log in again.')
            return

        try:
            client = get_discogs_client()
            user = client.identity()
        except Exception as e:
            yield send_error(f'Failed to connect to Discogs: {str(e)}')
            return

        # Step 2: Fetch collection (check cache first)
        cached = get_cached_collection(user.username)
        if cached is not None:
            collection_data = cached
            if len(collection_data) > MAX_COLLECTION_SIZE:
                yield send_error(f'Your collection has {len(collection_data)} albums. Collections over {MAX_COLLECTION_SIZE} albums are not currently supported.')
                return
            yield send_status('step-fetch', f'Using cached collection ({len(collection_data)} albums)')
            yield send_status('step-analyze', f'Analyzing {len(collection_data)} albums with AI...')
        else:
            yield send_status('step-fetch', f'Fetching collection for {user.username}...')

            try:
                collection_data = []
                try:
                    folders = user.collection_folders
                except Exception as e:
                    yield send_error(f'Could not access collection folders: {str(e)}')
                    return

                if not folders or len(folders) == 0:
                    yield send_error('Your Discogs collection appears to be empty.')
                    return

                # Folder 0 is the "All" folder containing every release.
                # Iterating all folders would double-count since other folders
                # are subsets of "All".
                all_folder = folders[0]

                album_count = 0
                try:
                    releases = all_folder.releases
                except Exception as e:
                    yield send_error(f'Could not access collection releases: {str(e)}')
                    return

                for item in releases:
                    try:
                        data = item.release.data

                        artists = [a['name'] for a in data.get('artists', [])] if data.get('artists') else []
                        artist_name = ', '.join(artists) if artists else 'Unknown Artist'

                        genres = data.get('genres', []) or []
                        genre = ', '.join(genres) if genres else ''

                        styles = data.get('styles', []) or []
                        style = ', '.join(styles) if styles else ''

                        labels = [l['name'] for l in data.get('labels', [])] if data.get('labels') else []
                        label_name = ', '.join(labels) if labels else ''

                        formats = []
                        for f in (data.get('formats', []) or []):
                            if isinstance(f, dict):
                                formats.append(f.get('name', ''))
                        format_str = ', '.join([f for f in formats if f]) if formats else ''

                        album_info = {
                            'artist': artist_name,
                            'album': data.get('title', ''),
                            'label': label_name,
                            'year': str(data.get('year', '')) if data.get('year') else '',
                            'genre': genre or style,
                            'format': format_str
                        }
                        collection_data.append(album_info)
                        album_count += 1

                        if album_count % 10 == 0:
                            yield send_status('step-fetch', f'Fetching collection... {album_count} albums found')

                    except Exception:
                        continue

                if not collection_data:
                    yield send_error('No albums found in your collection.')
                    return

                if len(collection_data) > MAX_COLLECTION_SIZE:
                    yield send_error(f'Your collection has {len(collection_data)} albums. Collections over {MAX_COLLECTION_SIZE} albums are not currently supported.')
                    return

                set_cached_collection(user.username, collection_data)
                yield send_status('step-analyze', f'Analyzing {len(collection_data)} albums with AI...')

            except Exception as e:
                yield send_error(f'Error fetching collection: {str(e)}')
                return

        # Step 3: LLM analysis
        try:
            analysis = analyze_collection_with_llm(collection_data)
        except Exception as e:
            yield send_error(f'Error during analysis: {str(e)}')
            return

        # Step 4: Save and complete
        yield send_status('step-done', 'Building your report...')
        # Can't write session from inside a streaming generator (headers
        # already sent), so stash in server-side dict for /results to pick up.
        sid = session.get('discogs_token', 'anon')
        pending_analysis[sid] = {
            'analysis': analysis,
            'collection_size': len(collection_data),
        }

        yield send_complete()

    return Response(stream_with_context(event_stream()), mimetype='text/event-stream',
                    headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no'})

@app.route('/results')
def results():
    """Display analysis results."""
    # Retrieve analysis from server-side store (written by SSE stream) or session fallback.
    sid = session.get('discogs_token', 'anon')
    pending = pending_analysis.pop(sid, None)
    if pending:
        analysis = pending['analysis']
        collection_size = pending['collection_size']
        # Persist into session for page refreshes.
        session['analysis'] = analysis
        session['collection_size'] = collection_size
    else:
        analysis = session.get('analysis')
        collection_size = session.get('collection_size', 0)

    if not analysis:
        return render_template('error.html', message='No analysis found. Please generate a report first.'), 400

    # Backward-compat: old analysis format lacks growth_areas; redirect to regenerate.
    if 'growth_areas' not in analysis:
        return redirect(url_for('index'))

    return render_template('results.html', analysis=analysis, collection_size=collection_size)

if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5000)
