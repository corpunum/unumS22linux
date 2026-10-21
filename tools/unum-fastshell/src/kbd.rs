//! On-screen keyboard: English (QWERTY) and Greek layouts plus a symbol layer.
//! Native, because squeekboard is a Wayland client of sway and cannot show
//! while the shell owns the panel directly.

#[derive(Clone, Copy, PartialEq, Eq, Debug)]
pub enum Layout {
    En,
    El,
    Sym,
}

pub const SHIFT: &str = "\u{21e7}";
pub const BKSP: &str = "\u{232b}";
pub const ENTER: &str = "\u{21b5}";
pub const SPACE: &str = " ";
pub const SYM: &str = "?123";
pub const ABC: &str = "ABC";
pub const LANG: &str = "\u{1f310}"; // drawn as "EN"/"ΕΛ"
pub const TONOS: &str = "\u{0384}";

const EN: [&[&str]; 4] = [
    &["q", "w", "e", "r", "t", "y", "u", "i", "o", "p"],
    &["a", "s", "d", "f", "g", "h", "j", "k", "l"],
    &[SHIFT, "z", "x", "c", "v", "b", "n", "m", BKSP],
    &[SYM, LANG, ",", SPACE, ".", ENTER],
];
const EL: [&[&str]; 4] = [
    &[";", "ς", "ε", "ρ", "τ", "υ", "θ", "ι", "ο", "π"],
    &["α", "σ", "δ", "φ", "γ", "η", "ξ", "κ", "λ", TONOS],
    &[SHIFT, "ζ", "χ", "ψ", "ω", "β", "ν", "μ", BKSP],
    &[SYM, LANG, ",", SPACE, ".", ENTER],
];
const SY: [&[&str]; 4] = [
    &["1", "2", "3", "4", "5", "6", "7", "8", "9", "0"],
    &["@", "#", "\u{20ac}", "&", "*", "-", "+", "(", ")", "/"],
    &["%", "!", "\"", "'", ":", ";", "?", "_", BKSP],
    &[ABC, LANG, ",", SPACE, ".", ENTER],
];

pub fn rows(l: Layout) -> [&'static [&'static str]; 4] {
    match l {
        Layout::En => EN,
        Layout::El => EL,
        Layout::Sym => SY,
    }
}

/// Width of a key in key units (a row is 10 units wide).
pub fn units(k: &str) -> f32 {
    match k {
        SHIFT | BKSP => 1.5,
        SYM | ABC => 1.5,
        LANG => 1.2,
        ENTER => 1.8,
        SPACE => 3.0,
        _ => 1.0,
    }
}

pub fn is_special(k: &str) -> bool {
    matches!(k, SHIFT | BKSP | ENTER | SYM | ABC | LANG | TONOS)
}

/// Greek vowel with tonos.
pub fn with_tonos(c: char) -> Option<char> {
    Some(match c {
        'α' => 'ά',
        'ε' => 'έ',
        'η' => 'ή',
        'ι' => 'ί',
        'ο' => 'ό',
        'υ' => 'ύ',
        'ω' => 'ώ',
        'Α' => 'Ά',
        'Ε' => 'Έ',
        'Η' => 'Ή',
        'Ι' => 'Ί',
        'Ο' => 'Ό',
        'Υ' => 'Ύ',
        'Ω' => 'Ώ',
        _ => return None,
    })
}

#[derive(Debug)]
pub struct Kbd {
    pub visible: bool,
    pub layout: Layout,
    pub text_layout: Layout,
    pub shift: bool,
    pub tonos: bool,
}

impl Default for Kbd {
    fn default() -> Self {
        Kbd { visible: false, layout: Layout::En, text_layout: Layout::En, shift: false, tonos: false }
    }
}

pub enum KeyOut {
    Text(String),
    Backspace,
    Enter,
    Redraw,
}

impl Kbd {
    pub fn label(&self, k: &str) -> String {
        match k {
            LANG => if self.text_layout == Layout::El { "ΕΛ".into() } else { "EN".into() },
            SPACE => if self.text_layout == Layout::El { "κενό".into() } else { "space".into() },
            SHIFT => "Shift".into(),
            BKSP => "Del".into(),
            ENTER => "Send".into(),
            SYM | ABC | TONOS => k.into(),
            _ if self.shift => k.to_uppercase(),
            _ => k.into(),
        }
    }

    pub fn press(&mut self, k: &str) -> KeyOut {
        match k {
            SHIFT => {
                self.shift = !self.shift;
                KeyOut::Redraw
            }
            BKSP => KeyOut::Backspace,
            ENTER => KeyOut::Enter,
            SYM => {
                self.layout = Layout::Sym;
                KeyOut::Redraw
            }
            ABC => {
                self.layout = self.text_layout;
                KeyOut::Redraw
            }
            LANG => {
                self.text_layout = if self.text_layout == Layout::En { Layout::El } else { Layout::En };
                self.layout = self.text_layout;
                self.tonos = false;
                KeyOut::Redraw
            }
            TONOS => {
                self.tonos = !self.tonos;
                KeyOut::Redraw
            }
            _ => {
                let mut s = if self.shift { k.to_uppercase() } else { k.to_string() };
                if self.tonos {
                    let mut cs = s.chars();
                    if let (Some(c), None) = (cs.next(), cs.next()) {
                        if let Some(t) = with_tonos(c) {
                            s = t.to_string();
                        }
                    }
                    self.tonos = false;
                }
                self.shift = false;
                KeyOut::Text(s)
            }
        }
    }
}
