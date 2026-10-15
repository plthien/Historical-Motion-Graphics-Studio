import { createTheme } from '@mui/material/styles';

export const studioTheme = createTheme({
  palette: {
    mode: 'dark',
    primary: { main: '#7C3AED', light: '#A78BFA', dark: '#5B21B6', contrastText: '#FFFFFF' },
    secondary: { main: '#53E6FF', light: '#8CF2FF', dark: '#0F8DA5', contrastText: '#06111A' },
    success: { main: '#22C55E' },
    warning: { main: '#F59E0B' },
    error: { main: '#EF4444' },
    background: {
      default: '#090C18',
      paper: '#151A2C',
    },
    text: {
      primary: '#F5F7FF',
      secondary: '#96A0BF',
    },
    divider: 'rgba(255,255,255,0.08)',
  },
  shape: {
    borderRadius: 22,
  },
  typography: {
    fontFamily: ['"Space Grotesk"', '"Manrope"', '"Segoe UI"', 'sans-serif'].join(','),
    h2: {
      fontWeight: 850,
      letterSpacing: '-0.055em',
    },
    h3: {
      fontWeight: 850,
      letterSpacing: '-0.05em',
    },
    h4: {
      fontWeight: 820,
      letterSpacing: '-0.04em',
    },
    h5: {
      fontWeight: 780,
      letterSpacing: '-0.025em',
    },
    h6: {
      fontWeight: 760,
      letterSpacing: '-0.02em',
    },
    button: {
      textTransform: 'none',
      fontWeight: 750,
    },
  },
  components: {
    MuiCssBaseline: {
      styleOverrides: {
        body: {
          background:
            'radial-gradient(circle at 18% 18%, rgba(124,58,237,0.26), transparent 28%), radial-gradient(circle at 86% 24%, rgba(17,116,139,0.34), transparent 32%), linear-gradient(180deg, #0A0D18 0%, #080B15 100%)',
        },
        '*': {
          boxSizing: 'border-box',
        },
      },
    },
    MuiCard: {
      styleOverrides: {
        root: {
          backgroundImage: 'linear-gradient(180deg, rgba(255,255,255,0.055), rgba(255,255,255,0.02))',
          backgroundColor: '#151A2C',
          border: '1px solid rgba(255,255,255,0.08)',
          boxShadow: '0 22px 70px rgba(0,0,0,0.26)',
        },
      },
    },
    MuiButton: {
      styleOverrides: {
        root: {
          borderRadius: 14,
          paddingLeft: 18,
          paddingRight: 18,
        },
      },
    },
    MuiChip: {
      styleOverrides: {
        root: {
          borderRadius: 10,
          fontWeight: 750,
        },
      },
    },
    MuiTextField: {
      styleOverrides: {
        root: {
          '& .MuiOutlinedInput-root': {
            borderRadius: 14,
            background: 'rgba(255,255,255,0.035)',
          },
        },
      },
    },
    MuiDrawer: {
      styleOverrides: {
        paper: {
          backgroundImage: 'linear-gradient(180deg, rgba(20,24,41,0.98), rgba(9,11,22,0.98))',
          borderLeft: '1px solid rgba(255,255,255,0.08)',
        },
      },
    },
  },
});
