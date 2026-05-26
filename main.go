package main

import (
	"fmt"
	"io"
	"net/http"
	"os"
	"strings"
	"sync"
	"time"
)

type CacheItem struct {
	Data       []byte
	Headers    http.Header
	Expiration time.Time
}

var cache sync.Map
var cacheDuration = 24 * time.Hour

// O mapa agora começa vazio
var meusAddons = make(map[string]string)

// Função que lê a variável do Render e preenche o mapa
func carregarAddons() {
	addonsEnv := os.Getenv("MEUS_ADDONS")
	if addonsEnv == "" {
		fmt.Println("Aviso: Variável de ambiente 'MEUS_ADDONS' não encontrada ou vazia.")
		return
	}

	// Separa cada addon pela vírgula
	pares := strings.Split(addonsEnv, ",")
	for _, par := range pares {
		// Separa a rota do link usando o sinal de igual (máximo 2 pedaços)
		kv := strings.SplitN(par, "=", 2)
		if len(kv) == 2 {
			rota := strings.TrimSpace(kv[0])
			link := strings.TrimSpace(kv[1])
			meusAddons[rota] = link
			fmt.Printf("Rotas carregadas no cache: %s -> %s\n", rota, link)
		}
	}
}

func proxyHandler(w http.ResponseWriter, r *http.Request) {
	var targetBaseURL string
	var pathRestante string

	// Verifica qual rota o usuário acessou
	for prefixo, urlOriginal := range meusAddons {
		if strings.HasPrefix(r.URL.Path, prefixo) {
			targetBaseURL = urlOriginal
			pathRestante = strings.TrimPrefix(r.URL.Path, prefixo)
			break
		}
	}

	if targetBaseURL == "" {
		http.Error(w, "Addon não encontrado neste servidor", http.StatusNotFound)
		return
	}

	targetURL := targetBaseURL + pathRestante
	if r.URL.RawQuery != "" {
		targetURL += "?" + r.URL.RawQuery
	}

	// Lógica de Cache
	if item, found := cache.Load(targetURL); found {
		cItem := item.(CacheItem)
		if time.Now().Before(cItem.Expiration) {
			for k, v := range cItem.Headers {
				w.Header()[k] = v
			}
			w.Header().Set("X-Cache", "HIT")
			w.Write(cItem.Data)
			return
		}
		cache.Delete(targetURL)
	}

	// Busca na API Original
	resp, err := http.Get(targetURL)
	if err != nil {
		http.Error(w, "Erro ao contatar o servidor original", http.StatusInternalServerError)
		return
	}
	defer resp.Body.Close()

	body, err := io.ReadAll(resp.Body)
	if err != nil {
		http.Error(w, "Erro ao ler a resposta", http.StatusInternalServerError)
		return
	}

	cache.Store(targetURL, CacheItem{
		Data:       body,
		Headers:    resp.Header.Clone(),
		    Expiration: time.Now().Add(cacheDuration),
	})

	for k, v := range resp.Header {
		w.Header()[k] = v
	}
	w.Header().Set("X-Cache", "MISS")
	w.Write(body)
}

func main() {
	// Carrega as URLs antes de iniciar o servidor
	carregarAddons()

	port := os.Getenv("PORT")
	if port == "" {
		port = "8080"
	}

	http.HandleFunc("/", proxyHandler)
	fmt.Println("Servidor Proxy rodando na porta", port)

	err := http.ListenAndServe(":"+port, nil)
	if err != nil {
		fmt.Println("Erro fatal no servidor:", err)
	}
}
