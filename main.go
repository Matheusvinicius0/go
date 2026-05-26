package main

import (
	"bufio"
	"bytes"
	"fmt"
	"io"
	"log"
	"net/http"
	"net/url"
	"strings"
)

// ProxyHandler recebe o pedido da TV
func ProxyHandler(w http.ResponseWriter, r *http.Request) {
	// Pega a URL original que a TV quer assistir via parâmetro
	// Ex: http://SEU_IP:11470/proxy?url=https://site.com/video.m3u8
	targetURLStr := r.URL.Query().Get("url")
	if targetURLStr == "" {
		http.Error(w, "Faltou o parâmetro 'url'", http.StatusBadRequest)
		return
	}

	targetURL, err := url.Parse(targetURLStr)
	if err != nil {
		http.Error(w, "URL inválida", http.StatusBadRequest)
		return
	}

	// 1. Cria a requisição para o servidor original
	req, err := http.NewRequest("GET", targetURLStr, nil)
	if err != nil {
		http.Error(w, err.Error(), http.StatusInternalServerError)
		return
	}

	// INJEÇÃO DE HEADERS (O Segredo para burlar o bloqueio)
	req.Header.Set("Referer", "https://7embeddecanais.xyz/")
	req.Header.Set("User-Agent", "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")

	// 2. Faz o download do arquivo
	client := &http.Client{}
	resp, err := client.Do(req)
	if err != nil {
		http.Error(w, "Erro ao conectar no servidor original", http.StatusBadGateway)
		return
	}
	defer resp.Body.Close()

	// 3. Verifica se é um arquivo de texto M3U8
	contentType := resp.Header.Get("Content-Type")
	if strings.Contains(contentType, "mpegurl") || strings.HasSuffix(targetURL.Path, ".m3u8") {
		// Se for M3U8, manda para a nossa função de reescrever
		rewriteM3U8(w, resp.Body, targetURL, r.Host)
		return
	}

	// 4. Se não for M3U8 (ex: for o arquivo .ts de vídeo puro)
	// Apenas repassa o vídeo direto para a TV (Streaming por Buffer)
	for k, vv := range resp.Header {
		for _, v := range vv {
			w.Header().Add(k, v)
		}
	}
	w.WriteHeader(resp.StatusCode)
	io.Copy(w, resp.Body) // Transfere os bytes em tempo real
}

// rewriteM3U8 lê o arquivo linha por linha e modifica as URLs
func rewriteM3U8(w http.ResponseWriter, body io.Reader, baseURL *url.URL, proxyHost string) {
	scanner := bufio.NewScanner(body)
	var rewrittenBody bytes.Buffer

	for scanner.Scan() {
		line := strings.TrimSpace(scanner.Text())

		// Se a linha for vazia ou for uma Tag do M3U8 (começa com #), mantemos igual
		if line == "" || strings.HasPrefix(line, "#") {
			rewrittenBody.WriteString(line + "\n")
			continue
		}

		// Se chegou aqui, é o link de um arquivo .ts ou de outro .m3u8 interno
		segmentURL, err := url.Parse(line)
		if err != nil {
			rewrittenBody.WriteString(line + "\n")
			continue
		}

		// Resolve links relativos. 
		// (Se o servidor mandou só "video_01.ts", o Go junta com a URL do M3U8 pai)
		absoluteURL := baseURL.ResolveReference(segmentURL).String()

		// Cria a nova URL mandando o arquivo passar pelo nosso proxy
		// url.QueryEscape garante que links complexos não quebrem o formato
		proxiedURL := fmt.Sprintf("http://%s/proxy?url=%s", proxyHost, url.QueryEscape(absoluteURL))

		// Escreve a nova linha reescrita
		rewrittenBody.WriteString(proxiedURL + "\n")
	}

	// Devolve o M3U8 modificado para a TV
	w.Header().Set("Content-Type", "application/vnd.apple.mpegurl")
	w.Header().Set("Access-Control-Allow-Origin", "*") // Evita bloqueio de CORS em alguns players
	w.WriteHeader(http.StatusOK)
	w.Write(rewrittenBody.Bytes())
}

func main() {
	http.HandleFunc("/proxy", ProxyHandler)
	log.Println("Proxy Fenixflix rodando na porta 11470...")
	// Escutando em 0.0.0.0 para aceitar conexões da TV na rede Wi-Fi
	log.Fatal(http.ListenAndServe("0.0.0.0:11470", nil))
}
