/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{ts,tsx}'],
  theme: {
    extend: {
      colors: {
        ink: '#1c1917',
        'ink-soft': '#3d382e',
        rice: '#f7f3ea',
        'rice-deep': '#e7dcc4',
        paper: '#fbf6ea',
        kraft: '#ede4d0',
        band: '#dcd0b4',
        line: '#d8cbad',
        muted: '#9a8f76',
        cinnabar: '#b23a2e',
        'cinnabar-deep': '#8a2b22',
        gold: '#b5883e',
        amber2: '#e0a94c',
        jade: '#3c6b54',
        azurite: '#2e5a6e',
      },
      fontFamily: {
        serif: ['"Noto Serif SC"', 'serif'],
        sans: ['"Noto Sans SC"', 'sans-serif'],
        brush: ['"Ma Shan Zheng"', 'cursive'],
      },
      boxShadow: {
        paper: '0 6px 20px rgba(120, 95, 40, 0.08)',
        'paper-lg': '0 20px 50px rgba(120, 95, 40, 0.16)',
      },
      keyframes: {
        'shy-spin': { to: { transform: 'rotate(360deg)' } },
        'shy-wave': { '0%,100%': { transform: 'scaleY(0.28)' }, '50%': { transform: 'scaleY(1)' } },
        'shy-rise': { from: { opacity: '0', transform: 'translateY(12px)' }, to: { opacity: '1', transform: 'none' } },
        'shy-pulse': { '0%,100%': { opacity: '0.35' }, '50%': { opacity: '1' } },
      },
      animation: {
        'shy-spin': 'shy-spin 1s linear infinite',
        'shy-rise': 'shy-rise 0.5s ease both',
        'shy-pulse': 'shy-pulse 1s ease-in-out infinite',
      },
    },
  },
  plugins: [],
}
