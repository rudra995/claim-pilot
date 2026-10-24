/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{js,jsx}'],
  theme: {
    extend: {
      fontFamily: {
        sans: ['Geist', 'Inter', 'ui-sans-serif', 'system-ui', 'sans-serif'],
        mono: ['"Geist Mono"', 'ui-monospace', 'monospace'],
      },
      colors: {
        canvas: '#f6f6f4',
        ink: {
          DEFAULT: '#18181b',
          soft: '#3f3f46',
          muted: '#71717a',
          faint: '#a1a1aa',
        },
        line: '#e7e7e4',
        accent: {
          50: '#eef6f4',
          100: '#d5ebe6',
          500: '#2f7d6d',
          600: '#256858',
          700: '#1d5246',
        },
      },
      borderRadius: {
        xl: '0.875rem',
      },
      boxShadow: {
        card: '0 1px 2px rgb(24 24 27 / 0.04)',
        lift: '0 8px 24px -8px rgb(24 24 27 / 0.12), 0 2px 6px rgb(24 24 27 / 0.04)',
        pop: '0 24px 64px -16px rgb(24 24 27 / 0.28)',
      },
      keyframes: {
        'fade-up': { from: { opacity: 0, transform: 'translateY(6px)' }, to: { opacity: 1, transform: 'none' } },
        'fade-in': { from: { opacity: 0 }, to: { opacity: 1 } },
        'scale-in': { from: { opacity: 0, transform: 'scale(0.97)' }, to: { opacity: 1, transform: 'none' } },
        'slide-in': { from: { opacity: 0, transform: 'translateX(24px)' }, to: { opacity: 1, transform: 'none' } },
        shimmer: { from: { backgroundPosition: '200% 0' }, to: { backgroundPosition: '-200% 0' } },
        breathe: { '0%, 100%': { opacity: 1 }, '50%': { opacity: 0.35 } },
        ring: { '0%': { boxShadow: '0 0 0 0 rgb(47 125 109 / 0.35)' }, '100%': { boxShadow: '0 0 0 10px rgb(47 125 109 / 0)' } },
      },
      animation: {
        'fade-up': 'fade-up 380ms cubic-bezier(0.2, 0.7, 0.2, 1) both',
        'fade-in': 'fade-in 240ms ease-out both',
        'scale-in': 'scale-in 220ms cubic-bezier(0.2, 0.7, 0.2, 1) both',
        'slide-in': 'slide-in 320ms cubic-bezier(0.2, 0.7, 0.2, 1) both',
        shimmer: 'shimmer 1.6s linear infinite',
        breathe: 'breathe 1.6s ease-in-out infinite',
        ring: 'ring 1.4s ease-out infinite',
      },
    },
  },
  plugins: [],
}
