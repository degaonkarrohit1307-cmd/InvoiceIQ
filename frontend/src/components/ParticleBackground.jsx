import { useEffect, useRef } from 'react'

// Lightweight canvas network with cursor-responsive node movement.
// The effect pauses when the page is hidden or reduced motion is requested.
function ParticleBackground() {
  const canvasRef = useRef(null)

  useEffect(() => {
    const canvas = canvasRef.current
    const ctx = canvas?.getContext('2d')
    if (!canvas || !ctx) return undefined

    const reducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)')
    const isMobile = window.innerWidth < 720
    const count = isMobile ? 34 : 76
    const linkDistance = isMobile ? 118 : 164
    const interactionRadius = isMobile ? 145 : 230

    let width = 0
    let height = 0
    let particles = []
    let rafId = 0
    let isVisible = document.visibilityState === 'visible'
    const pointer = {
      x: -1000,
      y: -1000,
      targetX: -1000,
      targetY: -1000,
      velocityX: 0,
      velocityY: 0,
      active: false,
    }

    const resize = () => {
      const ratio = Math.min(window.devicePixelRatio || 1, 2)
      width = canvas.clientWidth
      height = canvas.clientHeight
      canvas.width = Math.max(1, Math.floor(width * ratio))
      canvas.height = Math.max(1, Math.floor(height * ratio))
      ctx.setTransform(ratio, 0, 0, ratio, 0, 0)
    }

    const seed = () => {
      particles = Array.from({ length: count }, () => ({
        x: Math.random() * width,
        y: Math.random() * height,
        vx: (Math.random() - 0.5) * 0.24,
        vy: (Math.random() - 0.5) * 0.24,
        // Larger points make the neural structure easier to see without
        // turning the background into a field of bright, distracting stars.
        radius: 1.35 + Math.random() * 1.45,
      }))
    }

    const paint = (animate) => {
      ctx.clearRect(0, 0, width, height)

      if (animate && pointer.active) {
        pointer.x += (pointer.targetX - pointer.x) * 0.2
        pointer.y += (pointer.targetY - pointer.y) * 0.2
        pointer.velocityX *= 0.82
        pointer.velocityY *= 0.82
      }

      for (const particle of particles) {
        if (!animate) continue

        particle.x += particle.vx
        particle.y += particle.vy

        if (pointer.active) {
          const dx = particle.x - pointer.x
          const dy = particle.y - pointer.y
          const distance = Math.hypot(dx, dy)

          if (distance > 0 && distance < interactionRadius) {
            const proximity = 1 - distance / interactionRadius
            const influence = proximity * proximity
            // Nearby nodes gently move away from the pointer, while a small
            // amount of pointer momentum makes the connected network follow
            // the direction in which the cursor travels.
            const push = influence * 0.055
            particle.vx += (dx / distance) * push + pointer.velocityX * influence * 0.012
            particle.vy += (dy / distance) * push + pointer.velocityY * influence * 0.012
          }
        }

        const speed = Math.hypot(particle.vx, particle.vy)
        const maxSpeed = 0.78
        if (speed > maxSpeed) {
          particle.vx = (particle.vx / speed) * maxSpeed
          particle.vy = (particle.vy / speed) * maxSpeed
        }

        particle.vx *= 0.986
        particle.vy *= 0.986

        if (particle.x < -14) particle.x = width + 14
        if (particle.x > width + 14) particle.x = -14
        if (particle.y < -14) particle.y = height + 14
        if (particle.y > height + 14) particle.y = -14
      }

      // Draw the connecting edges first so the nodes remain crisp and visible.
      for (let i = 0; i < particles.length; i += 1) {
        for (let j = i + 1; j < particles.length; j += 1) {
          const a = particles[i]
          const b = particles[j]
          const dx = a.x - b.x
          const dy = a.y - b.y
          const distance = Math.hypot(dx, dy)

          if (distance < linkDistance) {
            const strength = 1 - distance / linkDistance
            let alpha = 0.13 + strength * 0.19
            let lineWidth = 0.65 + strength * 0.38

            if (pointer.active) {
              const midpointX = (a.x + b.x) / 2
              const midpointY = (a.y + b.y) / 2
              const pointerDistance = Math.hypot(midpointX - pointer.x, midpointY - pointer.y)
              if (pointerDistance < interactionRadius) {
                const response = 1 - pointerDistance / interactionRadius
                alpha += response * 0.12
                lineWidth += response * 0.25
              }
            }

            ctx.strokeStyle = `rgba(125, 211, 252, ${Math.min(alpha, 0.48)})`
            ctx.lineWidth = lineWidth
            ctx.beginPath()
            ctx.moveTo(a.x, a.y)
            ctx.lineTo(b.x, b.y)
            ctx.stroke()
          }
        }
      }

      if (pointer.active) {
        const glow = ctx.createRadialGradient(
          pointer.x, pointer.y, 0,
          pointer.x, pointer.y, interactionRadius,
        )
        glow.addColorStop(0, 'rgba(56, 189, 248, 0.13)')
        glow.addColorStop(0.48, 'rgba(56, 189, 248, 0.045)')
        glow.addColorStop(1, 'rgba(56, 189, 248, 0)')
        ctx.fillStyle = glow
        ctx.fillRect(
          pointer.x - interactionRadius,
          pointer.y - interactionRadius,
          interactionRadius * 2,
          interactionRadius * 2,
        )
      }

      // A restrained cyan halo around each larger node improves visibility.
      for (const particle of particles) {
        ctx.beginPath()
        ctx.arc(particle.x, particle.y, particle.radius * 2.5, 0, Math.PI * 2)
        ctx.fillStyle = 'rgba(56, 189, 248, 0.075)'
        ctx.fill()

        ctx.beginPath()
        ctx.arc(particle.x, particle.y, particle.radius, 0, Math.PI * 2)
        ctx.fillStyle = 'rgba(205, 239, 255, 0.92)'
        ctx.fill()
      }
    }

    const frame = () => {
      if (!isVisible) return
      paint(true)
      rafId = window.requestAnimationFrame(frame)
    }

    const handlePointer = (event) => {
      const rect = canvas.getBoundingClientRect()
      const nextX = event.clientX - rect.left
      const nextY = event.clientY - rect.top

      if (!pointer.active) {
        // Start the interaction at the current cursor position instead of
        // animating in from the off-canvas inactive position.
        pointer.x = nextX
        pointer.y = nextY
        pointer.velocityX = 0
        pointer.velocityY = 0
      } else {
        const deltaX = nextX - pointer.targetX
        const deltaY = nextY - pointer.targetY
        pointer.velocityX = Math.max(-32, Math.min(32, deltaX))
        pointer.velocityY = Math.max(-32, Math.min(32, deltaY))
      }

      pointer.targetX = nextX
      pointer.targetY = nextY
      pointer.active = true
    }

    const clearPointer = () => {
      pointer.active = false
      pointer.velocityX = 0
      pointer.velocityY = 0
    }

    const handleResize = () => {
      resize()
      paint(false)
    }

    const handleVisibility = () => {
      isVisible = document.visibilityState === 'visible'
      window.cancelAnimationFrame(rafId)
      if (isVisible && !reducedMotion.matches) {
        rafId = window.requestAnimationFrame(frame)
      }
    }

    const handleMotionChange = () => {
      window.cancelAnimationFrame(rafId)
      if (reducedMotion.matches) paint(false)
      else if (isVisible) rafId = window.requestAnimationFrame(frame)
    }

    resize()
    seed()
    paint(false)

    if (!reducedMotion.matches && isVisible) {
      rafId = window.requestAnimationFrame(frame)
    }

    window.addEventListener('resize', handleResize)
    window.addEventListener('pointermove', handlePointer, { passive: true })
    window.addEventListener('pointerleave', clearPointer)
    document.addEventListener('visibilitychange', handleVisibility)
    reducedMotion.addEventListener?.('change', handleMotionChange)

    return () => {
      window.cancelAnimationFrame(rafId)
      window.removeEventListener('resize', handleResize)
      window.removeEventListener('pointermove', handlePointer)
      window.removeEventListener('pointerleave', clearPointer)
      document.removeEventListener('visibilitychange', handleVisibility)
      reducedMotion.removeEventListener?.('change', handleMotionChange)
    }
  }, [])

  return <canvas ref={canvasRef} className="particle-canvas" aria-hidden="true" />
}

export default ParticleBackground
