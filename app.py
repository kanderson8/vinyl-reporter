import os
import csv
import io
from flask import Flask, render_template, request, jsonify, session
from openai import OpenAI
import json

# Try to load .env file if python-dotenv is available
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'dev-secret-key-change-in-production')

# Initialize OpenAI client lazily
def get_openai_client():
    """Get or create OpenAI client."""
    api_key = os.environ.get('OPENAI_API_KEY')
    if not api_key:
        raise ValueError("OPENAI_API_KEY environment variable is not set")
    return OpenAI(api_key=api_key)

def parse_discogs_csv(file_content):
    """Parse Discogs CSV file and extract collection data."""
    collection_data = []
    try:
        # Try to decode if it's bytes
        if isinstance(file_content, bytes):
            file_content = file_content.decode('utf-8')
        
        csv_reader = csv.DictReader(io.StringIO(file_content))
        for row in csv_reader:
            # Extract relevant fields (Discogs CSV typically has Artist, Album, Label, etc.)
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

def analyze_collection_with_llm(collection_data):
    """Use OpenAI to analyze the collection and generate insights."""
    
    # Prepare collection summary for LLM
    collection_summary = []
    for item in collection_data[:100]:  # Limit to first 100 for token efficiency
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
    """Main page with file upload form."""
    return render_template('index.html')

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
        # Read and parse CSV
        file_content = file.read()
        collection_data = parse_discogs_csv(file_content)
        
        if not collection_data:
            return jsonify({'error': 'CSV file appears to be empty or invalid'}), 400
        
        # Analyze with LLM
        analysis = analyze_collection_with_llm(collection_data)
        
        # Store in session for display
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
        return render_template('error.html', message='No analysis found. Please upload a collection first.'), 400
    
    return render_template('results.html', analysis=analysis, collection_size=collection_size)

if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5000)
