# Record Collection Analyzer

An AI-powered web application that analyzes your Discogs record collection and provides insights, recommendations, and improvements.

## Features

- **Direct Discogs Integration**: Login with your Discogs account and automatically fetch your collection
- **CSV Upload**: Alternative option to upload your Discogs collection CSV file
- AI-powered analysis of your collection's vibe and point of view
- Identification of collection strengths
- Suggestions for areas to improve
- 5 personalized album recommendations

## Requirements

- Python 3.8 or later
- OpenAI API key (get one at https://platform.openai.com/api-keys)
- Discogs API credentials (Consumer Key and Consumer Secret)
- Flask

## Setup

### 1. Register Your Application with Discogs

1. Navigate to [Discogs Developer Settings](https://www.discogs.com/settings/developers)
2. Click "Create an application"
3. Fill out the application details:
   - **Application Name**: Record Collection Analyzer (or your choice)
   - **Description**: AI-powered collection analysis
   - **Website URL**: Your app URL (or `http://localhost:5000` for development)
   - **Callback URL**: `http://localhost:5000/callback` (for local development)
   - **⚠️ IMPORTANT**: The callback URL must match EXACTLY what you set in `DISCOGS_CALLBACK_URL` environment variable (including http vs https, trailing slashes, etc.)
4. Save your **Consumer Key** and **Consumer Secret**

### 2. Install Dependencies

1. Create a virtual environment (recommended):
   ```bash
   python3 -m venv venv
   source venv/bin/activate
   ```

2. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```

### 3. Configure Environment Variables

Set up your environment variables:

```bash
export OPENAI_API_KEY="your_openai_api_key_here"
export DISCOGS_CONSUMER_KEY="your_discogs_consumer_key"
export DISCOGS_CONSUMER_SECRET="your_discogs_consumer_secret"
export DISCOGS_USER_AGENT="RecordCollectionAnalyzer/1.0"
export DISCOGS_CALLBACK_URL="http://localhost:5000/callback"
export SECRET_KEY="your_secret_key_here"  # Optional, defaults to dev key
```

Or create a `.env` file:
```bash
OPENAI_API_KEY=your_openai_api_key_here
DISCOGS_CONSUMER_KEY=your_discogs_consumer_key
DISCOGS_CONSUMER_SECRET=your_discogs_consumer_secret
DISCOGS_USER_AGENT=RecordCollectionAnalyzer/1.0
DISCOGS_CALLBACK_URL=http://localhost:5000/callback
SECRET_KEY=your_secret_key_here
```

**Note**: For production, update `DISCOGS_CALLBACK_URL` to your production callback URL.

### 4. Run the Server

```bash
python app.py
```

Note: Make sure your virtual environment is activated (you should see `(venv)` in your terminal prompt).

### 5. Open Your Browser

Visit `http://localhost:5000/` to start using the application.

## How to Use

### Option 1: Direct Discogs Integration (Recommended)

1. Click "Login with Discogs" on the main page
2. Authorize the application on Discogs
3. You'll be redirected back to the app
4. Click "Generate Report from Discogs"
5. Wait for the analysis (this may take 1-2 minutes for large collections)
6. View your personalized collection insights and recommendations

### Option 2: CSV Upload

1. Export your collection from Discogs as a CSV file
2. Visit the application in your browser
3. Upload your CSV file (drag & drop or click to select)
4. Wait for the AI analysis (this may take 30-60 seconds)
5. View your personalized collection insights and recommendations

## Endpoints

- `GET /` - Main page with Discogs login or file upload form
- `GET /login` - Initiate Discogs OAuth authentication
- `GET /callback` - OAuth callback handler
- `GET /logout` - Log out from Discogs
- `POST /generate-report` - Generate report from Discogs collection
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
- Discogs API requests are rate-limited (4 requests per second)
- Make sure your OpenAI API key has sufficient credits
- The server runs on `0.0.0.0:5000` by default
- Collection fetching may take time for large collections due to API rate limits

## Troubleshooting

### "Discogs API credentials not configured"
- Make sure `DISCOGS_CONSUMER_KEY` and `DISCOGS_CONSUMER_SECRET` are set

### "Not authenticated with Discogs"
- Click "Login with Discogs" and complete the OAuth flow

### Collection fetching is slow
- This is normal for large collections due to Discogs API rate limits (4 requests/second)
- The app includes rate limiting to respect Discogs API limits

### OAuth callback errors / Not redirecting back to app
- **Most common issue**: The callback URL in your Discogs app settings must match EXACTLY the `DISCOGS_CALLBACK_URL` environment variable
- Check that both use the same protocol (http vs https)
- Check that both have the same port number
- Check for trailing slashes - they must match
- For local development, use `http://localhost:5000/callback` in both places
- After updating the callback URL in Discogs settings, you may need to wait a few minutes for changes to take effect
- If you're still having issues, try editing your Discogs application and re-saving the callback URL
