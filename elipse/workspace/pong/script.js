// Elementos del canvas y puntuación
const canvas = document.getElementById("pongCanvas");
const ctx = canvas.getContext("2d");
const playerScoreElement = document.getElementById("playerScore");
const computerScoreElement = document.getElementById("computerScore");

// Configuración inicial
const playerHeight = 100;
const playerWidth = 15;
const computerHeight = 100;
const computerWidth = 15;
const ballSize = 15;
const paddleSpeed = 8;
const ballSpeedX = 7;
const ballSpeedY = 7;

// Posiciones iniciales
let playerY = canvas.height / 2 - playerHeight / 2;
let computerY = canvas.height / 2 - computerHeight / 2;
let ballX = canvas.width / 2;
let ballY = canvas.height / 2;
let ballDirectionX = -ballSpeedX;
let ballDirectionY = -ballSpeedY;

// Puntuación
let playerScore = 0;
let computerScore = 0;

// Mover la raqueta del jugador con las flechas
canvas.addEventListener("keydown", (e) => {
    if (e.key === "ArrowUp" && playerY > 0) {
        playerY -= paddleSpeed;
    } else if (e.key === "ArrowDown" && playerY < canvas.height - playerHeight) {
        playerY += paddleSpeed;
    }
});

// Función para dibujar todo
function draw() {
    // Limpiar el canvas
    ctx.fillStyle = "#000";
    ctx.fillRect(0, 0, canvas.width, canvas.height);

    // Dibujar la línea central
    ctx.strokeStyle = "#fff";
    ctx.lineWidth = 1;
    ctx.setLineDash([5, 5]);
    ctx.beginPath();
    ctx.moveTo(canvas.width / 2, 0);
    ctx.lineTo(canvas.width / 2, canvas.height);
    ctx.stroke();
    ctx.setLineDash([]);

    // Dibujar las raquetas
    ctx.fillStyle = "#fff";
    ctx.fillRect(10, playerY, playerWidth, playerHeight);
    ctx.fillRect(canvas.width - computerWidth - 10, computerY, computerWidth, computerHeight);

    // Dibujar la pelota
    ctx.beginPath();
    ctx.arc(ballX, ballY, ballSize, 0, Math.PI * 2);
    ctx.fill();

    // Lógica del juego
    // Mover la pelota
    ballX += ballDirectionX;
    ballY += ballDirectionY;

    // Colisiones con las paredes superior e inferior
    if (ballY <= 0 || ballY >= canvas.height) {
        ballDirectionY = -ballDirectionY;
    }

    // Colisión con las raquetas
    if (
        (ballX <= playerWidth + ballSize && 
         ballY >= playerY && 
         ballY <= playerY + playerHeight) ||
        (ballX >= canvas.width - computerWidth - ballSize && 
         ballY >= computerY && 
         ballY <= computerY + computerHeight)
    ) {
        ballDirectionX = -ballDirectionX;
        // Aumentar velocidad ligeramente con cada rebote
        ballDirectionX *= 1.02;
        ballDirectionY *= 1.02;
    }

    // Puntuación
    if (ballX <= 0) {
        computerScore++;
        computerScoreElement.textContent = computerScore;
        resetBall();
    } else if (ballX >= canvas.width) {
        playerScore++;
        playerScoreElement.textContent = playerScore;
        resetBall();
    }

    // IA para el computador (sigue la pelota)
    const computerCenterY = computerY + computerHeight / 2;
    if (computerCenterY < ballY - 10) {
        computerY += paddleSpeed;
    } else if (computerCenterY > ballY + 10) {
        computerY -= paddleSpeed;
    }

    // Asegurar que las raquetas no salgan del canvas
    if (computerY < 0) computerY = 0;
    if (computerY > canvas.height - computerHeight) computerY = canvas.height - computerHeight;

    // Dibujar de nuevo
    requestAnimationFrame(draw);
}

// Reiniciar la pelota al centro
function resetBall() {
    ballX = canvas.width / 2;
    ballY = canvas.height / 2;
    ballDirectionX = -ballDirectionX * 0.8; // Reducir velocidad al reiniciar
    ballDirectionY = Math.random() > 0.5 ? ballSpeedY : -ballSpeedY;
}

// Iniciar el juego
draw();