package main

import (
	"bytes"
	"encoding/base64"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"os"
)

type OpenAIRequest struct {
	Model     string    `json:"model"`
	Messages  []Message `json:"messages"`
	MaxTokens int       `json:"max_tokens"`
}

type Message struct {
	Role    string        `json:"role"`
	Content []ContentPart `json:"content"`
}

type ContentPart struct {
	Type     string    `json:"type"`
	Text     string    `json:"text,omitempty"`
	ImageURL *ImageURL `json:"image_url,omitempty"`
}

type ImageURL struct {
	URL string `json:"url"`
}

type OpenAIResponse struct {
	Choices []struct {
		Message struct {
			Content string `json:"content"`
		} `json:"message"`
	} `json:"choices"`
}

func analyzeImage(imagePath, apiKey string) (string, error) {
	// Read the image file
	imageData, err := os.ReadFile(imagePath)
	if err != nil {
		return "", fmt.Errorf("failed to read image: %w", err)
	}

	// Encode to base64
	base64Image := base64.StdEncoding.EncodeToString(imageData)
	dataURL := fmt.Sprintf("data:image/jpeg;base64,%s", base64Image)

	// Prepare the request
	reqBody := OpenAIRequest{
		Model: "gpt-4o",
		Messages: []Message{
			{
				Role: "user",
				Content: []ContentPart{
					{
						Type: "text",
						Text: "What's in this image? Please describe it in detail.",
					},
					{
						Type: "image_url",
						ImageURL: &ImageURL{
							URL: dataURL,
						},
					},
				},
			},
		},
		MaxTokens: 500,
	}

	jsonData, err := json.Marshal(reqBody)
	if err != nil {
		return "", fmt.Errorf("failed to marshal request: %w", err)
	}

	// Make the API request
	req, err := http.NewRequest("POST", "https://api.openai.com/v1/chat/completions", bytes.NewBuffer(jsonData))
	if err != nil {
		return "", fmt.Errorf("failed to create request: %w", err)
	}

	req.Header.Set("Content-Type", "application/json")
	req.Header.Set("Authorization", fmt.Sprintf("Bearer %s", apiKey))

	client := &http.Client{}
	resp, err := client.Do(req)
	if err != nil {
		return "", fmt.Errorf("failed to send request: %w", err)
	}
	defer resp.Body.Close()

	body, err := io.ReadAll(resp.Body)
	if err != nil {
		return "", fmt.Errorf("failed to read response: %w", err)
	}

	if resp.StatusCode != http.StatusOK {
		return "", fmt.Errorf("API error (%d): %s", resp.StatusCode, string(body))
	}

	// Parse the response
	var openAIResp OpenAIResponse
	if err := json.Unmarshal(body, &openAIResp); err != nil {
		return "", fmt.Errorf("failed to parse response: %w", err)
	}

	if len(openAIResp.Choices) == 0 {
		return "", fmt.Errorf("no response from API")
	}

	return openAIResp.Choices[0].Message.Content, nil
}

func main() {
	if len(os.Args) < 2 {
		fmt.Println("Usage: go run image_analyzer.go <path_to_image.jpg>")
		os.Exit(1)
	}

	imagePath := os.Args[1]

	// Check if file exists
	if _, err := os.Stat(imagePath); os.IsNotExist(err) {
		fmt.Printf("Error: Image file '%s' not found\n", imagePath)
		os.Exit(1)
	}

	// Get API key from environment
	apiKey := os.Getenv("OPENAI_API_KEY")
	if apiKey == "" {
		fmt.Println("Error: OPENAI_API_KEY environment variable not set")
		os.Exit(1)
	}

	description, err := analyzeImage(imagePath, apiKey)
	if err != nil {
		fmt.Printf("Error: %v\n", err)
		os.Exit(1)
	}

	fmt.Println("\n--- Image Analysis ---")
	fmt.Println(description)
}
