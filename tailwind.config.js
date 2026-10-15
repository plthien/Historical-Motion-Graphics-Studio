/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{ts,tsx}'],
  theme: {
    extend: {
      colors: {
        cockpit: {
          950: '#050816',
          900: '#0b1120',
          850: '#101827',
          800: '#172033',
          750: '#1b273a',
          700: '#22314a',
          600: '#314666',
          500: '#4b6792',
          400: '#8ba2c7',
        },
      },
      boxShadow: {
        panel: '0 24px 80px rgba(2, 6, 23, 0.45)',
        soft: '0 12px 28px rgba(2, 6, 23, 0.22)',
      },
      borderRadius: {
        xl2: '1.25rem',
      },
      fontFamily: {
        sans: ['"IBM Plex Sans"', '"Segoe UI"', 'sans-serif'],
        mono: ['"IBM Plex Mono"', '"Cascadia Mono"', '"Consolas"', 'monospace'],
      },
      keyframes: {
        'fade-up': {
          '0%': { opacity: '0', transform: 'translateY(12px)' },
          '100%': { opacity: '1', transform: 'translateY(0)' },
        },
      },
      animation: {
        'fade-up': 'fade-up 320ms ease-out both',
      },
    },
  },
  plugins: [],
};
