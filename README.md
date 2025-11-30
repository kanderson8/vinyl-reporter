# Record Collection Analyzer

An AI-powered web application that analyzes your Discogs record collection and provides insights, recommendations, and improvements.

## Features

- Upload your Discogs collection CSV file
- AI-powered analysis of your collection's vibe and point of view
- Identification of collection strengths
- Suggestions for areas to improve
- 5 personalized album recommendations

## Requirements

- Python 3.8 or later
- OpenAI API key (get one at https://platform.openai.com/api-keys)
- Flask
- Discogs collection exported as CSV

## Setup

1. Create a virtual environment (recommended):
   ```bash
   python3 -m venv venv
   source venv/bin/activate
   ```

2. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```

3. Set up environment variables:
   ```bash
   export OPENAI_API_KEY="your_openai_api_key_here"
   export SECRET_KEY="your_secret_key_here"  # Optional, defaults to dev key
   ```
   
   Or create a `.env` file (you'll need python-dotenv package):
   ```bash
   OPENAI_API_KEY=your_openai_api_key_here
   SECRET_KEY=your_secret_key_here
   ```

4. Run the server:
   ```bash
   python app.py
   ```

   Note: Make sure your virtual environment is activated (you should see `(venv)` in your terminal prompt).

5. Open your browser and visit:
   - `http://localhost:5000/` to upload your collection CSV

## How to Use

1. Export your collection from Discogs as a CSV file
2. Visit the application in your browser
3. Upload your CSV file (drag & drop or click to select)
4. Wait for the AI analysis (this may take 30-60 seconds)
5. View your personalized collection insights and recommendations

## Endpoints

- `GET /` - Main page with file upload form
- `POST /upload` - Upload CSV file and generate analysis
- `GET /results` - Display analysis results

## CSV Format

The application expects a Discogs CSV export with columns such as:
- Artist
- Album (or Title)
- Year
- Genre
- Label
- Format

The parser is flexible and will work with common variations of these column names.

## Notes

- The application uses OpenAI's GPT-4o-mini model for analysis
- Large collections are sampled (first 100 albums) for token efficiency
- Make sure your OpenAI API key has sufficient credits
- The server runs on `0.0.0.0:5000` by default
